import pytest

from queue_engine import QueueError

F = "a.gcode.3mf"


def ids(q):
    return [j["id"] for j in q.jobs]


def test_reorder(make):
    a, b, c = (make.add(n + ".gcode.3mf", target="S1") for n in "abc")
    make.reorder(c["id"], 0)
    assert ids(make) == [c["id"], a["id"], b["id"]]
    make.reorder(c["id"], 99)
    assert ids(make) == [a["id"], b["id"], c["id"]]


def test_any_matches_model(make):
    job = make.add(F, target="any", model="P1S")
    assert make.next_for("S1") is None
    assert make.next_for("S2")["id"] == job["id"]


def test_any_matches_filament_type_and_colour(make, fake):
    make.add(F, target="any", model="H2S", filaments=[{"type": "PETG", "colour": "#FF0000"}])
    fake.trays["S1"] = [{"type": "PETG", "colour": "#00FF00"}]
    fake.trays["S3"] = [{"type": "PETG", "colour": "#ff0000"}]
    assert make.next_for("S1") is None
    assert make.next_for("S3") is not None


def test_refuses_when_running(make, fake):
    make.add(F, target="S1")
    fake.states["sensor.one_print_status"] = "running"
    with pytest.raises(QueueError):
        make.start("S1", plate_clear=True)
    assert not [c for c in fake.calls if c[1] == "print_file"]


def test_requires_plate_clear(make):
    make.add(F, target="S1")
    with pytest.raises(QueueError):
        make.start("S1")
    with pytest.raises(QueueError):
        make.start("S1", plate_clear="yes")


def test_guard_refusal_keeps_job_first_with_error(make, fake):
    first = make.add(F, target="S1")
    make.add("b.gcode.3mf", target="S1")
    fake.fail[("druckwerk", "print_file")] = "G-code guard: no filament"
    with pytest.raises(QueueError, match="G-code guard"):
        make.start("S1", plate_clear=True)
    assert make.jobs[0]["id"] == first["id"]
    assert make.jobs[0]["state"] == "queued"
    assert "G-code guard" in make.jobs[0]["error"]


def test_pause_entities_reenabled_after_failed_send(make, fake):
    make.add(F, target="S1")
    fake.fail[("druckwerk", "print_file")] = "boom"
    with pytest.raises(QueueError):
        make.start("S1", plate_clear=True)
    assert fake.states["automation.snap"] == "on"
    assert [c[1] for c in fake.calls] == ["turn_off", "print_file", "turn_on"]


def test_send_uses_force_and_options(make, fake):
    make.add(F, target="S1", options={"timelapse": True})
    make.start("S1", plate_clear=True)
    data = [c for c in fake.calls if c[1] == "print_file"][0][2]
    assert data == {"serial": "S1", "file": F, "timelapse": True, "bed_leveling": True,
                    "use_ams": True, "force": True}


def start_job(make):
    make.add(F, target="S1")
    return make.start("S1", plate_clear=True)


def test_running_with_idle_stage_and_no_tray_is_not_started(make, fake):
    job = start_job(make)
    fake.states.update({"sensor.one_print_status": "running", "sensor.one_current_stage": "idle",
                        "sensor.one_active_tray": "none", "sensor.one_print_progress": "5"})
    make.tick("S1")
    assert job["state"] == "starting"


def test_changing_filament_with_tray_zero_starts(make, fake):
    job = start_job(make)
    fake.states.update({"sensor.one_current_stage": "changing_filament", "sensor.one_active_tray": "0"})
    make.tick("S1")
    assert job["state"] == "printing"


def test_printing_with_progress_and_tray_starts(make, fake):
    job = start_job(make)
    fake.states.update({"sensor.one_current_stage": "printing", "sensor.one_active_tray": "1",
                        "sensor.one_print_progress": "0"})
    make.tick("S1")
    assert job["state"] == "starting"
    fake.states["sensor.one_print_progress"] = "2"
    make.tick("S1")
    assert job["state"] == "printing"


def test_short_filament_change_seen_via_observe(make, fake):
    job = start_job(make)
    make.observe("S1", "changing_filament")
    fake.states.update({"sensor.one_current_stage": "printing", "sensor.one_active_tray": "2",
                        "sensor.one_print_progress": "0"})
    make.tick("S1")
    assert job["state"] == "printing"


def test_timeout_fails_with_reason(make, fake):
    job = start_job(make)
    fake.states.update({"sensor.one_print_status": "running", "sensor.one_current_stage": "idle",
                        "sensor.one_active_tray": "none"})
    fake.now += 599
    make.tick("S1")
    assert job["state"] == "starting"
    fake.now += 2
    make.tick("S1")
    assert job["state"] == "failed"
    assert "stage idle" in job["error"] and "tray none" in job["error"]
    assert make.history[-1]["id"] == job["id"]


def test_done_and_retry(make, fake):
    job = start_job(make)
    fake.states.update({"sensor.one_current_stage": "changing_filament", "sensor.one_active_tray": "0"})
    make.tick("S1")
    fake.states["sensor.one_print_status"] = "finish"
    make.tick("S1")
    assert job["state"] == "done"
    with pytest.raises(QueueError):
        make.retry(job["id"])


def test_cancel_and_retry(make):
    job = make.add(F, target="S1")
    make.cancel(job["id"])
    assert not make.jobs and make.history[-1]["state"] == "cancelled"
    assert make.retry(job["id"])["state"] == "queued"


def test_history_capped_and_persisted(make, tmp_path, fake):
    for _ in range(205):
        make.cancel(make.add(F, target="S1")["id"])
    assert len(make.history) == 200
    from queue_engine import Queue
    again = Queue(make.registry, str(tmp_path), fake.get_state, fake.call_service)
    assert len(again.history) == 200
