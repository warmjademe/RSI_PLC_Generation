"""Versioned training assets, paired admission decisions, and audit history."""
from pathlib import Path
import fcntl
import json
import time
import tempfile

from baseline_common.binding import seal_adapter, verify_adapter
from baseline_common.errors import ProtocolError
from baseline_common.memory import freeze, load
from baseline_common.utils import object_hash
from experiments.deepseek_study.run import save
from .rsi_protocol import assert_training_sources, record_sources, subset_corpus
from .rsi_evidence import verify_outcome, verified_practice, learning_context_sources
from .training import records_from_corpus


def gate_decision(parent, candidate, protocol):
    expected=set(protocol['development_ids'])
    for batch in [parent,candidate]:
        for row in batch:verify_outcome(row,protocol)
        if len(batch)!=len(expected) or {r['task_id'] for r in batch}!=expected:
            raise ProtocolError('promotion must evaluate the complete fixed development partition')
        if any(r['role']!='development' or r.get('candidates',0)>protocol['max_candidates'] for r in batch):
            raise ProtocolError('invalid gate role or candidate budget')
        if any(r['status'] not in ['pass','fail','unknown'] or type(r['tokens']) is not int or r['tokens']<0 for r in batch):
            raise ProtocolError('invalid gate outcome or token usage')
    old={r['task_id']:r for r in parent};new={r['task_id']:r for r in candidate}
    if len({r['evaluation_binding_sha256'] for r in parent+candidate}) != 1:
        raise ProtocolError('paired evaluation changed model, dataset, evaluator, or budget')
    lost=[tid for tid in sorted(expected) if old[tid]['status']=='pass' and new[tid]['status']!='pass']
    gains=sum(r['status']=='pass' for r in candidate)-sum(r['status']=='pass' for r in parent)
    old_tokens=sum(r['tokens'] for r in parent);new_tokens=sum(r['tokens'] for r in candidate)
    unknown_delta=sum(r['status']=='unknown' for r in candidate)-sum(r['status']=='unknown' for r in parent)
    metered=all(r.get('estimated_charge_calls',0)==0 for r in parent+candidate)
    enough_savings=new_tokens<=old_tokens*(1-protocol['promotion']['equal_success_min_token_reduction'])
    accept=(metered and not lost and unknown_delta<=0 and
            ((gains>0 and new_tokens<=old_tokens) or (gains==0 and enough_savings and new_tokens<old_tokens)))
    return {'accepted':accept,'success_delta':gains,'lost_successes':lost,'unknown_delta':unknown_delta,'fully_metered':metered,
            'parent_tokens':old_tokens,'candidate_tokens':new_tokens,
            'development_results_sha256':object_hash({'parent':parent,'candidate':candidate}),
            'parent_results':parent,'candidate_results':candidate,
            'policy':protocol['promotion']}


class VersionStore:
    def __init__(self, root, protocol, corpus):
        self.root=Path(root);self.protocol=protocol;self.corpus=corpus
        self.root.mkdir(parents=True,exist_ok=True)
        binding=self.root/'protocol.json'
        if binding.exists() and json.loads(binding.read_text())!=protocol:
            raise ProtocolError('RSI protocol changed across recovery')
        if not binding.exists():save(binding,protocol)
        self.seed_records={r['id']:r for r in records_from_corpus(corpus)}

    def _validate_records(self, records, parent):
        previous={r['id']:r for r in self.records(parent)} if parent else {}
        available={r['id']:r for r in records}
        for record in records:
            assert_training_sources(record_sources(record),self.protocol)
            if record == previous.get(record['id']) or record == self.seed_records.get(record['id']):
                continue
            if record['kind']=='verified_skill':
                from .skills import verify_skill
                verify_skill(record,available,self.protocol)
            elif record['kind'] in ['verified_program','successful_trajectory_repair']:
                result=verified_practice(record['rsi_evidence'],self.protocol)
                if record.get('learning_context_task_ids')!=learning_context_sources(record['rsi_evidence'],self.protocol):
                    raise ProtocolError('new memory omitted or changed its transitive training context')
                field='code' if record['kind']=='verified_program' else 'after_code'
                if record[field]!=result['code'] or record['task_id']!=result['task_id']:
                    raise ProtocolError('new memory does not match its verified training program')
                original=next(r for r in self.corpus[1] if r['task_id']==record['task_id'])
                if any(record[k]!=original[k] for k in ['requirement','interface','metadata','target']):
                    raise ProtocolError('new memory changed its source public contract')
                if record['kind']=='successful_trajectory_repair':
                    from baseline_common.datasets import sha
                    proof=record['repair_source_receipt'];path=Path(proof['path'])
                    if sha(path)!=proof['sha256'] or sha(path.with_name('request.json'))!=proof['request_sha256']:
                        raise ProtocolError('repair failure receipt changed')
                    failed=json.loads(path.read_text());request=json.loads(path.with_name('request.json').read_text())
                    if failed['status']!='fail' or request['code']!=record['before_code'] or request['task']['id']!=record['task_id']:
                        raise ProtocolError('repair has no matching confirmed earlier failure')
            else:raise ProtocolError('unrecognized or ungrounded RSI record')

    def create(self, records, settings, *, parent=None, round_number=0, changes=None):
        self._validate_records(records,parent)
        if len({r['id'] for r in records})!=len(records):raise ProtocolError('duplicate asset record identity')
        lineage={'parent':parent,'round':round_number,'records_sha256':object_hash(records),
                 'settings':settings,'changes':changes or {},'protocol_sha256':self.protocol['protocol_sha256']}
        version='v'+str(round_number).zfill(3)+'_'+object_hash(lineage)[:16]
        path=self.root/'versions'/version
        if path.exists():
            verify_adapter(path,'OurMethod')
            if json.loads((path/'version.json').read_text())!=lineage:raise ProtocolError('version identity collision')
            return version
        if parent is not None:verify_adapter(self.root/'versions'/parent,'OurMethod')
        allowed=set().union(*(record_sources(r) for r in records)) if records else set()
        corpus=subset_corpus(self.corpus,allowed)
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='.build-',dir=path.parent))/'asset'
        freeze(temporary,method='OurMethod',records=records,corpus=corpus[0],settings=settings)
        save(temporary/'version.json',lineage)
        seal_adapter(temporary,'OurMethod',corpus,settings,{'version':version,'round':round_number,'record_count':len(records)})
        temporary.rename(path);temporary.parent.rmdir()
        return version

    def records(self, version):
        path=self.root/'versions'/version;verify_adapter(path,'OurMethod')
        return load(path,'OurMethod')[1]

    def publish(self, version, *, parent, gate=None):
        verify_adapter(self.root/'versions'/version,'OurMethod')
        lineage=json.loads((self.root/'versions'/version/'version.json').read_text())
        if lineage['parent']!=parent:raise ProtocolError('promotion parent does not match asset lineage')
        if parent is not None:
            self.check_gate(version,parent,gate)
            if not gate['accepted']:raise ProtocolError('noninitial version requires an accepted development gate')
        with (self.root/'registry.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            current=self.root/'current.json'
            previous=json.loads(current.read_text()) if current.exists() else None
            if previous and previous['version']==version:return previous
            if (previous['version'] if previous else None)!=parent:
                raise ProtocolError('current version changed during candidate evaluation')
            event={'version':version,'parent':parent,'gate':gate,'epoch':time.time(),
                   'previous_event_sha256':previous['event_sha256'] if previous else '0'*64}
            event['event_sha256']=object_hash(event)
            save(self.root/'decisions'/(version+'.json'),event)
            save(current,event)
            return event

    def check_gate(self, version, parent, gate):
        if not gate or any(k not in gate for k in ['parent_results','candidate_results']):
            raise ProtocolError('missing paired development evidence')
        if any(r['version']!=parent for r in gate['parent_results']) or any(r['version']!=version for r in gate['candidate_results']):
            raise ProtocolError('development evidence evaluates a different asset version')
        if gate_decision(gate['parent_results'],gate['candidate_results'],self.protocol)!=gate:
            raise ProtocolError('admission decision does not reproduce from its receipts')

    def reject(self, version, *, parent, gate):
        self.check_gate(version,parent,gate)
        if gate['accepted']:raise ProtocolError('accepted candidate cannot be recorded as rejected')
        save(self.root/'rejections'/(version+'.json'),{'version':version,'parent':parent,'gate':gate})

    def rollback(self, version, *, reason):
        """Explicit rollback to a previously admitted version; preserve the decision chain."""
        verify_adapter(self.root/'versions'/version,'OurMethod')
        decision=self.root/'decisions'/(version+'.json')
        if not decision.exists() or not reason:raise ProtocolError('rollback needs a prior admission and reason')
        with (self.root/'registry.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            previous=json.loads((self.root/'current.json').read_text())
            event={'version':version,'parent':previous['version'],'operation':'rollback','reason':reason,
                   'epoch':time.time(),'previous_event_sha256':previous['event_sha256']}
            event['event_sha256']=object_hash(event)
            save(self.root/'rollbacks'/(event['event_sha256']+'.json'),event)
            save(self.root/'current.json',event)
            return event

    def view(self, version, excluded, output):
        records=[r for r in self.records(version) if not record_sources(r)&set(excluded)]
        ids=set().union(*(record_sources(r) for r in records)) if records else set()
        corpus=subset_corpus(self.corpus,ids)
        settings=json.loads((self.root/'versions'/version/'version.json').read_text())['settings']
        output=Path(output)
        binding={'version':version,'excluded_ids':sorted(excluded),'protocol_sha256':self.protocol['protocol_sha256']}
        if output.exists():
            verify_adapter(output,'OurMethod')
            if json.loads((output/'view.json').read_text())!=binding:raise ProtocolError('asset view changed')
            return output
        output.parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='.view-',dir=output.parent))/'asset'
        freeze(temporary,method='OurMethod',records=records,corpus=corpus[0],settings=settings)
        save(temporary/'view.json',binding)
        seal_adapter(temporary,'OurMethod',corpus,settings,{'source_version':version,'excluded_ids':sorted(excluded)})
        temporary.rename(output);temporary.parent.rmdir()
        return output
