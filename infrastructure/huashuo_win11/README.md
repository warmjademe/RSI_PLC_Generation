# Huashuo Windows 11 PLC validation terminals

This directory defines a reproducible template-and-clone deployment for three
Windows 11 PLC engineering terminals on `qyb-HuaShuo`.

## Runtime layout

- Template web console: `http://192.168.2.3:8100`
- Template RDP: `192.168.2.3:33900`
- Terminal 01: web `8101`, RDP `33901`
- Terminal 02: web `8102`, RDP `33902`
- Terminal 03: web `8103`, RDP `33903`
- Runtime root on Huashuo: `/home/qyb/plc-win11`
- Credentials: `/home/qyb/plc-win11/.env` with mode `0600`

Each VM uses 8 vCPUs, 16 GiB RAM, a q35 machine with Secure Boot/TPM, an
e1000e network adapter, and a 96 GiB qcow2 disk. These settings mirror the
existing Kemei Windows validation VM defaults. The runtime image is the
official `ghcr.io/dockur/windows` image resolved from manifest digest
`sha256:0cff9eb0e7aee9953e55bc682852ca4fdca233145a58ae1ec94f0b0c01a2ed30`
and imported on Huashuo as `plc-win11-runtime:0cff9eb0`.

The container time zone is pinned to `Asia/Hong_Kong`. Dockur boots QEMU with
an RTC based on container local time; leaving the container at UTC makes the
Chinese Windows guests run eight hours behind after a cold boot.

## Deployment sequence

1. Run `start_template.sh` and wait for Windows 11 installation to finish.
2. During Windows setup, `C:\OEM\install.bat` launches
   `Install-DeltaTools.ps1`. The script verifies both installer hashes, installs
   ISPSoft 3.24 and COMMGR 2.11 without an automatic reboot, and writes
   `delta-install-report.json` to the shared folder. Installers are mounted
   read-only as `Z:\installers`. The script copies them to
   `C:\DeltaPLCInstall`, verifies the local SHA-256 values, and launches them
   locally so embedded MSI payloads are not blocked by UNC-path restrictions.
   If unattended installation needs to be retried, run the same PowerShell
   script manually from an elevated session.
3. Shut the template Windows guest down cleanly and stop its container.
4. Run `clone_terminals.sh`. It refuses to overwrite any existing terminal.
5. Run `status.sh` and validate RDP and application startup on all three
   terminals. Project compilation and COMMGR Simulator canaries are a separate
   admission stage after the validation projects and worker service are staged.

`Inspect-Win11Template.ps1` is a non-mutating in-guest diagnostic. It records
OEM log output, relevant running processes, installed Delta products, expected
paths, and the identity task in `template-diagnostic.json` on the shared drive.
The matching `.cmd` wrapper allows the diagnostic to be launched directly from
Explorer. `Install-DeltaTools-Elevated.cmd` is the manual elevated retry entry.
The Delta installers used here still display prerequisite, license, and signed
driver confirmations even when their outer launchers receive silent flags.
Complete those prompts once on the template; clones inherit the installed
state. After reboot, `Validate-DeltaTools.cmd` launches and closes ISPSoft and
COMMGR and writes `delta-runtime-validation.json`.

The template disk is retained as the immutable cloning source. Do not run the
template concurrently with clones produced from it.

The three clones share the same validated software image. On their first boot,
the scheduled `DeltaPLC-ConfigureTerminalIdentity` task reads the per-terminal
`terminal-identity.json`, assigns a distinct Windows computer name and worker
identity, reboots once, and writes `terminal-identity-report.json`. This avoids
collisions when the terminals later join the same validation queue.

## Verified deployment (2026-08-23)

| Terminal | Windows identity | Worker identity | RDP | Web console | Result |
|---|---|---|---:|---:|---|
| `plc-win11-01` | `PLC-WIN11-01` | `huashuo-01` | `33901` | `8101` | `ok` |
| `plc-win11-02` | `PLC-WIN11-02` | `huashuo-02` | `33902` | `8102` | `ok` |
| `plc-win11-03` | `PLC-WIN11-03` | `huashuo-03` | `33903` | `8103` | `ok` |

All three terminals run Windows 11 Pro build 26200 with China Standard Time,
ISPSoft 3.24, and COMMGR 2.11.0.14. RDP authentication succeeded on every
terminal. Each `delta-runtime-validation.json` records successful, responding
ISPSoft and COMMGR processes; terminal 01 was also visually checked for the
full Chinese ISPSoft workspace and the COMMGR main window with Simulator,
DIACom, and eComm controls.

Evidence is retained under
`/home/qyb/plc-win11/shared/terminal-01`, `terminal-02`, and `terminal-03` on
Huashuo. The stopped template passed `qemu-img check` with no errors. The
failed UNC-launch attempt and the partial first clone are retained under
`shared/template/runs` and `failed-clones` respectively.

This deployment verifies the Windows/Delta engineering-tool layer. It does not
yet claim an ISPSoft project compilation or COMMGR Simulator canary; those
checks require staging the target PLC projects and worker harness.
