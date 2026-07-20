# Linbo image manager

This module provides an object to manage all linbo images, backups and extra files (rename, delete, ... ).
The manager contains a dict of all groups in the attributes `groups`. Each group is a `LinboImageGroup` which lists all files, backups contained in the directory. You can get a dict of this description with the method `to_dict()` like bellow: 

```Console
>>> from linuxmusterTools.linbo import LinboImageManager
>>> lim = LinboImageManager()
>>> lim.groups
{'ubuntu': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b6443a0>, 'focal': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b645f00>, 'test-linbo42': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b25f7f0>, 'data': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b25f7c0>}
>>> lim.rename('test-linbo42', 'test-linbo101')
>>> lim.groups
{'ubuntu': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b6443a0>, 'focal': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b645f00>, 'data': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b25f7c0>, 'test-linbo101': <linuxmusterTools.linbo.images.LinboImageGroup object at 0x7f579b61a500>}
>>> lim.groups['ubuntu'].to_dict()
{'name': 'ubuntu', 'size': 2609718272, 'desc': 'Install LaTeX and update', 'info': '["ubuntu.qcow2" Info File]\ntimestamp="202108291639"\nimage="ubuntu.qcow2"\nimagesize="2609718272"\npartition="/dev/sda1"\npartitionsize="31457280"\n', 'reg': None, 'postsync': None, 'vdi': None, 'prestart': '#! /bin/bash\n\necho "ok"\n', 'backup': False, 'diff': False, 'timestamp': '202108291639', 'date': '29/08/2021 16:39', 'diff_image': {}, 'backups': {'03/03/2022 19:03': {'name': 'ubuntu', 'size': 0, 'desc': '', 'info': 'timestamp=202203031903\ndate=voila', 'reg': None, 'postsync': None, 'vdi': None, 'prestart': None, 'backup': True, 'diff': False, 'timestamp': '202203031903', 'date': '03/03/2022 19:03'}, '29/08/2021 16:22': {'name': 'ubuntu', 'size': 3233778176, 'desc': 'Install ZSH', 'info': '[ubuntu.qcow2 Info File]\ntimestamp=202108291622\nimage=ubuntu.qcow2\nbaseimage=/dev/sda1\npartitionsize=31457159\nimagesize=3233778176\n', 'reg': None, 'postsync': None, 'vdi': None, 'prestart': '#! /bin/bash\n\necho "ok"\n', 'backup': True, 'diff': False, 'timestamp': '202108291622', 'date': '29/08/2021 16:22'}, '29/08/2021 16:17': {'name': 'ubuntu', 'size': 3233778176, 'desc': 'Install ZSH', 'info': '[ubuntu.qcow2 Info File]\ntimestamp=202108291617\nimage=ubuntu.qcow2\nbaseimage=/dev/sda1\npartitionsize=31457159\nimagesize=3233778176\n', 'reg': None, 'postsync': None, 'vdi': None, 'prestart': '#! /bin/bash\n\necho "ok"\n', 'backup': True, 'diff': False, 'timestamp': '202108291617', 'date': '29/08/2021 16:17'}}, 'selected': False}
```

## Windows driver profiles

`LinboDriverManager` owns hardware-specific Windows driver profiles below
`/srv/linbo/drivers`. `LinboImageManager` owns their image assignments and
the generated companion dispatcher below `/srv/linbo/images`.

The files have deliberately narrow roles:

- `drivers/<profile>/match.conf` describes one DMI hardware class.
- `drivers/<profile>/image.conf` assigns that profile to one image.
- `images/<image>/<image>.driverpostsync` dispatches the profile list.

Each profile contains exactly one DMI vendor and one product substring:

```ini
[match]
vendor = LENOVO
product = 21L4
```

The values use the same case-sensitive semantics as LINBO: the vendor must
match exactly and `product` must occur in the client's DMI product name. An
explicit `*` can be used as a wildcard. A short product such as `21L4` can
therefore cover multiple variants of the same hardware class.

The assignment is also a canonical LMN config object:

```ini
[image]
name = win11
```

The former standalone package's flat `image = win11` form remains readable
for upgrades. The next assignment writes the canonical section form.

```python
from linuxmusterTools.linbo import LinboDriverManager, LinboImageManager

drivers = LinboDriverManager()
drivers.create_profile("lenovo-21l4", "LENOVO", "21L4")
drivers.update_match("lenovo-21l4", "LENOVO", "21L4S")

images = LinboImageManager(driver_manager=drivers)
images.assign_driver_profile("lenovo-21l4", "win11")
images.reconcile_driverpostsyncs()
```

Many profiles may reference the same image. Every assignment change rebuilds
one deterministic, sorted dispatcher for that image. Removing the last
assignment keeps a cleanup dispatcher so a client can discard stale state.
`reconcile_driverpostsyncs()` repairs missing or manually changed managed
dispatchers from the `image.conf` assignments. It also migrates dispatchers
with an exact known legacy ownership header and leaves unrelated hooks alone.

The dispatcher contains no matching or transfer implementation. It only calls
the static `linbo_driverpostsync` command with the image and profile names.
Roll out the corresponding LINBO client-filesystem command before assigning
profiles; otherwise the generated command guard reports the missing runtime.

Administrators place prepared INF driver payloads next to `match.conf`.
Profile updates preserve these files. Uploading, extracting and inspecting
payload archives are intentionally outside this interface.

`LMNFile` can leave hidden `.match.conf.bak.*` and `.image.conf.bak.*`
metadata on updates. The LINBO runtime packaging change must exclude those
server-side backups from transferred driver payloads.
