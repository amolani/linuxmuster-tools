from pathlib import Path

import pytest

import linuxmusterTools.linbo.images as images_module
from linuxmusterTools.linbo.drivers import (
    DriverProfileAssignedError,
    LinboDriverManager,
)
from linuxmusterTools.linbo.images import (
    DRIVERPOSTSYNC_LEGACY_HEADERS,
    DRIVERPOSTSYNC_MANAGED_HEADER,
    DriverPostsyncOwnershipError,
    LinboImage,
    LinboImageManager,
)


def _create_image(root, name, *, backup=None, payload=b"image"):
    directory = root / name
    if backup is not None:
        directory /= f"backups/{backup}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.qcow2").write_bytes(payload)
    (directory / f"{name}.qcow2.info").write_text(
        f"timestamp={backup or '202607200900'}\n"
        f"image={name}.qcow2\n"
        f"imagesize={len(payload)}\n"
        "partition=/dev/sda1\n"
        "partitionsize=1000\n"
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    drivers = LinboDriverManager(tmp_path / "drivers")
    monkeypatch.setattr(images_module, "LINBO_PATH", str(images))
    monkeypatch.setattr(LinboImage, "_torrent_stop", lambda self: None)

    def manager(*image_names):
        for name in image_names:
            _create_image(images, name)
        return drivers, LinboImageManager(driver_manager=drivers), images

    return manager


def _profile(drivers, name):
    return drivers.create_profile(name, "Vendor", "Product")


@pytest.mark.parametrize("image", ["0012", "yes", "no"])
def test_assignment_is_canonical_and_preserves_exact_string(environment, image):
    drivers, images, _ = environment(image)
    profile = _profile(drivers, "numeric")

    assert images.assign_driver_profile("numeric", image) == {
        "profile": "numeric",
        "image": image,
    }
    assert Path(profile["path"], "image.conf").read_text() == (
        f"[image]\nname = {image}\n"
    )
    assert images.get_driver_profile_image("numeric") == image


def test_legacy_flat_assignment_is_read_and_canonicalized(environment):
    drivers, images, root = environment("win11")
    profile = _profile(drivers, "legacy")
    image_conf = Path(profile["path"], "image.conf")
    image_conf.write_text(
        "# Image assignment for driver profile\nimage = win11\n"
    )

    assert images.get_driver_profile_image("legacy") == "win11"
    assert images.reconcile_driverpostsyncs() == {
        "updated": ["win11"],
        "unchanged": [],
        "ignored": [],
        "missing": [],
    }
    assert 'linbo_driverpostsync "win11" "legacy"' in (
        root / "win11/win11.driverpostsync"
    ).read_text()

    images.assign_driver_profile("legacy", "win11")

    assert image_conf.read_text() == "[image]\nname = win11\n"


def test_legacy_flat_assignment_can_be_unassigned(environment):
    drivers, images, _ = environment("win11")
    profile = _profile(drivers, "legacy")
    image_conf = Path(profile["path"], "image.conf")
    image_conf.write_text("image = win11\n")

    assert images.unassign_driver_profile("legacy") == {
        "profile": "legacy",
        "image": None,
    }
    assert not image_conf.exists()


def test_many_profiles_render_one_sorted_thin_dispatcher(environment):
    drivers, images, root = environment("win11")
    for profile in ("zeta", "Alpha", "beta"):
        _profile(drivers, profile)
        images.assign_driver_profile(profile, "win11")

    content = (root / "win11/win11.driverpostsync").read_text()

    assert f"{DRIVERPOSTSYNC_MANAGED_HEADER}\n" in content
    assert "# Profiles: Alpha, beta, zeta" in content
    assert 'linbo_driverpostsync "win11" "Alpha" "beta" "zeta"' in content
    assert "command -v linbo_driverpostsync" in content
    assert "dmidecode" not in content
    assert "pnputil" not in content
    assert len(content.splitlines()) < 20


def test_move_regenerates_old_new_and_last_profile_cleanup(environment):
    drivers, images, root = environment("old", "new")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "old")

    images.assign_driver_profile("model", "new")

    assert 'linbo_driverpostsync "old"\n' in (
        root / "old/old.driverpostsync"
    ).read_text()
    assert "# Profiles: (none)" in (
        root / "old/old.driverpostsync"
    ).read_text()
    assert 'linbo_driverpostsync "new" "model"' in (
        root / "new/new.driverpostsync"
    ).read_text()

    images.unassign_driver_profile("model")

    cleanup = (root / "new/new.driverpostsync").read_text()
    assert "# Profiles: (none)" in cleanup
    assert 'linbo_driverpostsync "new"\n' in cleanup


def test_missing_profile_or_image_does_not_mutate_metadata(environment):
    drivers, images, root = environment("win11")
    profile = _profile(drivers, "model")

    with pytest.raises(FileNotFoundError):
        images.assign_driver_profile("missing", "win11")
    with pytest.raises(FileNotFoundError):
        images.assign_driver_profile("model", "missing")

    assert not Path(profile["path"], "image.conf").exists()
    assert not (root / "win11/win11.driverpostsync").exists()


def test_assignment_rejects_runtime_unsupported_dotted_image(environment):
    drivers, images, _ = environment("win.11")
    profile = _profile(drivers, "model")

    with pytest.raises(ValueError, match="only"):
        images.assign_driver_profile("model", "win.11")

    assert not Path(profile["path"], "image.conf").exists()


def test_assignment_rejects_runtime_image_name_over_100_characters(environment):
    image = "a" * 101
    drivers, images, root = environment(image)
    profile = _profile(drivers, "model")

    with pytest.raises(ValueError, match="at most 100"):
        images.assign_driver_profile("model", image)

    assert not Path(profile["path"], "image.conf").exists()
    assert not (root / image / f"{image}.driverpostsync").exists()


@pytest.mark.parametrize(
    "content",
    [
        "image = win11\nextra = value\n",
        "[image]\nname = win11\nextra = value\n",
        "[image] # comment\nname = win11\n",
        "[image]\nname = win11 # comment\n",
    ],
)
def test_malformed_image_conf_is_rejected(environment, content):
    drivers, images, _ = environment("win11")
    profile = _profile(drivers, "model")
    Path(profile["path"], "image.conf").write_text(content)

    with pytest.raises(ValueError):
        images.get_driver_profile_image("model")


def test_symlink_image_conf_is_not_followed(environment, tmp_path):
    drivers, images, _ = environment("win11")
    profile = _profile(drivers, "model")
    outside = tmp_path / "outside.conf"
    outside.write_text("[image]\nname = win11\n")
    Path(profile["path"], "image.conf").symlink_to(outside)

    with pytest.raises(ValueError, match="not a regular file"):
        images.get_driver_profile_image("model")

    assert outside.read_text() == "[image]\nname = win11\n"


@pytest.mark.parametrize("hook_type", ["foreign", "symlink", "directory"])
def test_assignment_refuses_unmanaged_or_nonregular_hook(
    environment, tmp_path, hook_type
):
    drivers, images, root = environment("win11")
    profile = _profile(drivers, "model")
    hook = root / "win11/win11.driverpostsync"
    if hook_type == "foreign":
        hook.write_text("#!/bin/sh\necho keep\n")
    elif hook_type == "symlink":
        outside = tmp_path / "outside.sh"
        outside.write_text("keep")
        hook.symlink_to(outside)
    else:
        hook.mkdir()

    with pytest.raises(DriverPostsyncOwnershipError):
        images.assign_driver_profile("model", "win11")

    assert not Path(profile["path"], "image.conf").exists()


@pytest.mark.parametrize("legacy_header", sorted(DRIVERPOSTSYNC_LEGACY_HEADERS))
def test_assignment_migrates_exact_legacy_ownership_header(
    environment, legacy_header
):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    hook = root / "win11/win11.driverpostsync"
    hook.write_text(f"#!/bin/sh\n{legacy_header}\necho legacy\n")

    images.assign_driver_profile("model", "win11")

    content = hook.read_text()
    assert DRIVERPOSTSYNC_MANAGED_HEADER in content.splitlines()
    assert legacy_header not in content.splitlines()
    assert 'linbo_driverpostsync "win11" "model"' in content


def test_similar_but_unknown_legacy_header_is_not_accepted(environment):
    drivers, images, root = environment("win11")
    profile = _profile(drivers, "model")
    hook = root / "win11/win11.driverpostsync"
    content = "# Managed-By: linuxmusterTools.linbo.driver_hooks v10\n"
    hook.write_text(content)

    with pytest.raises(DriverPostsyncOwnershipError):
        images.assign_driver_profile("model", "win11")

    assert hook.read_text() == content
    assert not Path(profile["path"], "image.conf").exists()


def test_reconcile_repairs_missing_and_manually_changed_managed_hooks(
    environment, monkeypatch
):
    drivers, images, root = environment("assigned", "cleanup", "foreign")
    _profile(drivers, "assigned-model")
    _profile(drivers, "cleanup-model")
    images.assign_driver_profile("assigned-model", "assigned")
    images.assign_driver_profile("cleanup-model", "cleanup")
    images.unassign_driver_profile("cleanup-model")

    assigned_hook = root / "assigned/assigned.driverpostsync"
    cleanup_hook = root / "cleanup/cleanup.driverpostsync"
    foreign_hook = root / "foreign/foreign.driverpostsync"
    assigned_hook.unlink()
    cleanup_hook.write_text(
        f"#!/bin/sh\n{DRIVERPOSTSYNC_MANAGED_HEADER}\necho changed\n"
    )
    foreign_content = "#!/bin/sh\necho keep\n"
    foreign_hook.write_text(foreign_content)

    result = images.reconcile_driverpostsyncs()

    assert result == {
        "updated": ["assigned", "cleanup"],
        "unchanged": [],
        "ignored": ["foreign"],
        "missing": [],
    }
    assert 'linbo_driverpostsync "assigned" "assigned-model"' in (
        assigned_hook.read_text()
    )
    assert 'linbo_driverpostsync "cleanup"\n' in cleanup_hook.read_text()
    assert foreign_hook.read_text() == foreign_content
    assigned_content = assigned_hook.read_bytes()
    cleanup_content = cleanup_hook.read_bytes()

    def unexpected_write(*_args):
        pytest.fail("idempotent reconciliation attempted a write")

    monkeypatch.setattr(images, "_atomic_replace", unexpected_write)
    assert images.reconcile_driverpostsyncs() == {
        "updated": [],
        "unchanged": ["assigned", "cleanup"],
        "ignored": ["foreign"],
        "missing": [],
    }
    assert assigned_hook.read_bytes() == assigned_content
    assert cleanup_hook.read_bytes() == cleanup_content
    assert foreign_hook.read_text() == foreign_content


def test_reconcile_preflights_all_assigned_hooks_before_publication(environment):
    drivers, images, root = environment("a-image", "z-image")
    _profile(drivers, "a-model")
    _profile(drivers, "z-model")
    images.assign_driver_profile("a-model", "a-image")
    images.assign_driver_profile("z-model", "z-image")
    a_hook = root / "a-image/a-image.driverpostsync"
    z_hook = root / "z-image/z-image.driverpostsync"
    a_hook.unlink()
    z_content = "#!/bin/sh\necho foreign\n"
    z_hook.write_text(z_content)

    with pytest.raises(DriverPostsyncOwnershipError):
        images.reconcile_driverpostsyncs()

    assert not a_hook.exists()
    assert z_hook.read_text() == z_content


def test_reconcile_publication_failure_rolls_back_all_hooks(
    environment, monkeypatch
):
    drivers, images, root = environment("a-image", "z-image")
    for image in ("a-image", "z-image"):
        _profile(drivers, f"{image}-model")
        images.assign_driver_profile(f"{image}-model", image)
    hooks = {
        image: root / image / f"{image}.driverpostsync"
        for image in ("a-image", "z-image")
    }
    for image, hook in hooks.items():
        hook.write_text(
            f"#!/bin/sh\n{DRIVERPOSTSYNC_MANAGED_HEADER}\necho {image}\n"
        )
    snapshots = {image: hook.read_bytes() for image, hook in hooks.items()}
    original = images._atomic_replace
    calls = []

    def fail_second(path, content, mode):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("publication failed")
        return original(path, content, mode)

    monkeypatch.setattr(images, "_atomic_replace", fail_second)

    with pytest.raises(OSError, match="publication failed"):
        images.reconcile_driverpostsyncs()

    assert {image: hook.read_bytes() for image, hook in hooks.items()} == snapshots


def test_publication_failure_rolls_back_assignment_and_both_hooks(
    environment, monkeypatch
):
    drivers, images, root = environment("a-new", "z-old")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "z-old")
    old_hook = (root / "z-old/z-old.driverpostsync").read_bytes()
    original = images._regenerate_driverpostsync
    calls = []

    def fail_second(image):
        calls.append(image)
        if len(calls) == 2:
            raise OSError("publication failed")
        return original(image)

    monkeypatch.setattr(images, "_regenerate_driverpostsync", fail_second)

    with pytest.raises(OSError, match="publication failed"):
        images.assign_driver_profile("model", "a-new")

    assert images.get_driver_profile_image("model") == "z-old"
    assert (root / "z-old/z-old.driverpostsync").read_bytes() == old_hook
    assert not (root / "a-new/a-new.driverpostsync").exists()


def test_assigned_profile_cannot_be_deleted(environment):
    drivers, images, _ = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")

    with pytest.raises(DriverProfileAssignedError):
        drivers.delete_profile("model")


def test_assigned_image_rename_and_delete_are_guarded(environment):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")

    with pytest.raises(DriverProfileAssignedError):
        images.rename("win11", "renamed")
    with pytest.raises(DriverProfileAssignedError):
        images.delete("win11")

    assert "win11" in images.groups
    assert (root / "win11/win11.qcow2").exists()


def test_malformed_profile_blocks_image_delete(environment):
    drivers, images, root = environment("win11")
    profile = _profile(drivers, "broken")
    Path(profile["path"], "match.conf").write_text("invalid = true\n")
    Path(profile["path"], "image.conf").write_text("[image]\nname = win11\n")

    with pytest.raises(ValueError):
        images.delete("win11")

    assert (root / "win11/win11.qcow2").exists()


def test_duplicate_does_not_copy_dispatcher_or_assignment(environment):
    drivers, images, root = environment("source")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "source")

    images.duplicate("source", "clone")

    assert (root / "clone/clone.qcow2").exists()
    assert not list((root / "clone").glob("*.driverpostsync"))
    assert images.get_driver_profile_image("model") == "source"


def test_restore_preserves_the_current_dispatcher(environment):
    drivers, images, root = environment("win11")
    _create_image(root, "win11", backup="202607190800", payload=b"backup")
    images.list()
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")
    hook = root / "win11/win11.driverpostsync"
    content = hook.read_bytes()

    images.restore("win11", "19/07/2026 08:00")

    assert hook.read_bytes() == content
    assert (root / "win11/win11.qcow2").read_bytes() == b"backup"


def test_unassigned_managed_hook_follows_rename_and_delete(environment):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")
    images.unassign_driver_profile("model")

    images.rename("win11", "windows")

    hook = root / "windows/windows.driverpostsync"
    assert 'linbo_driverpostsync "windows"\n' in hook.read_text()
    assert not (root / "win11").exists()

    images.delete("windows")
    assert not (root / "windows").exists()


def test_rename_publication_failure_leaves_image_and_old_hook_unchanged(
    environment, monkeypatch
):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")
    images.unassign_driver_profile("model")
    hook = root / "win11/win11.driverpostsync"
    hook_content = hook.read_bytes()

    def fail_publication(*_args):
        raise OSError("publication failed")

    monkeypatch.setattr(images, "_atomic_replace", fail_publication)

    with pytest.raises(OSError, match="publication failed"):
        images.rename("win11", "windows")

    assert (root / "win11/win11.qcow2").exists()
    assert hook.read_bytes() == hook_content
    assert not (root / "windows").exists()
    assert "win11" in images.groups


def test_failed_image_rename_restores_old_dispatcher(environment, monkeypatch):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")
    images.unassign_driver_profile("model")
    hook = root / "win11/win11.driverpostsync"
    hook_content = hook.read_bytes()

    def fail_rename(_new_name):
        raise OSError("rename failed")

    monkeypatch.setattr(images.groups["win11"], "rename", fail_rename)

    with pytest.raises(OSError, match="rename failed"):
        images.rename("win11", "windows")

    assert hook.read_bytes() == hook_content
    assert not (root / "win11/windows.driverpostsync").exists()
    assert (root / "win11/win11.qcow2").exists()


def test_failed_image_delete_restores_dispatcher(environment, monkeypatch):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")
    images.unassign_driver_profile("model")
    hook = root / "win11/win11.driverpostsync"
    hook_content = hook.read_bytes()

    def fail_delete():
        raise OSError("delete failed")

    monkeypatch.setattr(images.groups["win11"], "delete", fail_delete)

    with pytest.raises(OSError, match="delete failed"):
        images.delete("win11")

    assert hook.read_bytes() == hook_content
    assert "win11" in images.groups


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_foreign_dispatcher_blocks_image_lifecycle(environment, operation):
    _, images, root = environment("win11")
    hook = root / "win11/win11.driverpostsync"
    content = "#!/bin/sh\necho keep\n"
    hook.write_text(content)

    with pytest.raises(DriverPostsyncOwnershipError):
        if operation == "delete":
            images.delete("win11")
        else:
            images.rename("win11", "windows")

    assert hook.read_text() == content
    assert (root / "win11/win11.qcow2").exists()


def test_managed_hook_prevents_rename_to_runtime_unsupported_name(environment):
    drivers, images, root = environment("win11")
    _profile(drivers, "model")
    images.assign_driver_profile("model", "win11")
    images.unassign_driver_profile("model")

    with pytest.raises(ValueError, match="only"):
        images.rename("win11", "win.11")

    assert (root / "win11/win11.driverpostsync").exists()


def test_orphan_assignment_can_be_removed(environment):
    drivers, images, _ = environment("win11")
    profile = _profile(drivers, "model")
    image_conf = Path(profile["path"], "image.conf")
    image_conf.write_text("[image]\nname = vanished\n")

    images.assign_driver_profile("model", "win11")
    assert images.get_driver_profile_image("model") == "win11"

    image_conf.write_text("[image]\nname = vanished\n")

    assert images.unassign_driver_profile("model") == {
        "profile": "model",
        "image": None,
    }
    assert not image_conf.exists()
