from linuxmusterTools.linbo.images import LinboImageManager


class FakeImage:
    def __init__(self):
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeImageGroup(FakeImage):
    def __init__(self, backups=None):
        super().__init__()
        self.backups = backups or {}
        self.loaded = False

    def load(self):
        self.loaded = True


def _manager(group):
    manager = object.__new__(LinboImageManager)
    manager.groups = {"win11": group}
    return manager


def test_delete_without_date_removes_whole_image_group():
    group = FakeImageGroup()
    manager = _manager(group)

    manager.delete("win11")

    assert group.deleted is True
    assert "win11" not in manager.groups


def test_delete_with_known_date_removes_only_backup():
    backup = FakeImage()
    backup_date = "21/07/2026 10:00"
    group = FakeImageGroup({backup_date: backup})
    manager = _manager(group)

    manager.delete("win11", date=backup_date)

    assert backup.deleted is True
    assert group.loaded is True
    assert group.deleted is False
    assert manager.groups["win11"] is group


def test_delete_with_unknown_date_keeps_whole_image_group():
    group = FakeImageGroup()
    manager = _manager(group)

    manager.delete("win11", date="missing")

    assert group.deleted is False
    assert group.loaded is False
    assert manager.groups["win11"] is group
