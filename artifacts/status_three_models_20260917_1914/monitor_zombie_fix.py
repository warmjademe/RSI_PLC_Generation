"""Operational sidecar: exclude zombies from the descendant freeze loop."""
import inspect
from baseline8_online200_watchdog import processes, monitor, speed_monitor


def install():
    source=inspect.getsource(processes.terminate_tree)
    old='if q["ppid"] in selected and q["pid"] not in selected:'
    new='if q["state"] != "Z" and q["ppid"] in selected and q["pid"] not in selected:'
    assert source.count(old)==1, 'unexpected process cleanup implementation'
    exec(compile(source.replace(old,new),__file__,'exec'),processes.__dict__)
    monitor.terminate_tree=processes.terminate_tree
    speed_monitor.terminate_tree=processes.terminate_tree


if __name__=='__main__':
    install()
    from baseline8_quantized200_monitor_v4.monitor import main
    main()
