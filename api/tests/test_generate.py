from tests.conftest import H
from tests.test_projects import make


def gen(c, **body):
    r = c.post("/api/generate", json={"prompt": "a goblin archer", **body}, headers=H)
    return r


def test_generate_queues_jobs_with_upload_urls(world, alice):
    _, _, sched, _ = world
    r = gen(alice, preset="sprite", count=2, seed=10)
    assert r.status_code == 202, r.text
    arts = r.json()["artifacts"]
    assert [a["status"] for a in arts] == ["queued", "queued"] and [a["seed"] for a in arts] == [10, 11]
    assert arts[0]["project"]["name"] == "Scratch" and arts[0]["model"] == "flux2-klein-4b"
    model, inputs = sched.submitted[0]
    assert model == "flux2-klein-4b" and len(inputs) == 2
    assert inputs[0]["prompt"].startswith("pixel art game sprite of a goblin archer")
    assert arts[0]["full_prompt"] == inputs[0]["prompt"] and arts[0]["prompt"] == "a goblin archer"
    assert inputs[0]["output_url"].startswith(f"http://s3.test/b/artifacts/{arts[0]['id']}/original.png")
    assert inputs[1]["seed"] == 11


def test_poller_completes_uploaded_and_inline_images(world, alice):
    app, _, sched, store = world
    a1, a2 = gen(alice, count=2).json()["artifacts"]
    sched.jobs["job-1"] = {"status": "running"}
    assert app.state.poller.poll_once() == 2
    assert alice.get(f"/api/artifacts/{a1['id']}").json()["status"] == "running"
    store.objects[f"artifacts/{a1['id']}/original.png"] = b"\x89PNG uploaded by the scheduler"
    sched.finish("job-1", uploaded=True, seed=777)
    sched.finish("job-2", uploaded=False)
    assert app.state.poller.poll_once() == 0
    done = alice.get(f"/api/artifacts/{a1['id']}").json()
    assert (done["status"], done["seed"], done["worker"], done["seconds"]) == ("done", 777, "ainode", 2.9)
    assert alice.get(done["image_url"]).content == b"\x89PNG uploaded by the scheduler"
    img = alice.get(f"/api/artifacts/{a2['id']}/image?download=1")
    assert img.content.startswith(b"\x89PNG") and "attachment" in img.headers["content-disposition"]


def test_failed_job_records_the_error(world, alice):
    app, _, sched, _ = world
    a = gen(alice).json()["artifacts"][0]
    sched.jobs["job-1"] = {"status": "failed", "error": "sd-server refused the job"}
    app.state.poller.poll_once()
    got = alice.get(f"/api/artifacts/{a['id']}").json()
    assert got["status"] == "failed" and "refused" in got["error"] and got["image_url"] is None


def test_scheduler_down_refuses_and_leaves_nothing(world, alice):
    sched = world[2]
    sched.down = True
    r = gen(alice)
    assert r.status_code == 503
    assert alice.get("/api/artifacts").json()["artifacts"] == []


def test_bad_requests(alice):
    assert gen(alice, model="sdxl").status_code == 422
    assert gen(alice, preset="nope").status_code == 422
    assert gen(alice, width=1000).status_code == 422
    assert gen(alice, count=5).status_code == 422


def test_pending_cap(alice):
    assert gen(alice, count=4).status_code == 202
    assert gen(alice, count=3).status_code == 429                 # cap is 6 in the test settings
    assert gen(alice, count=2).status_code == 202


def test_private_artifacts_stay_private_public_ones_are_shared(world, alice, bob):
    app, _, sched, _ = world
    pub = make(alice, "shared", "public")
    priv = gen(alice).json()["artifacts"][0]
    shared = gen(alice, project_id=pub["id"]).json()["artifacts"][0]
    sched.finish("job-1", uploaded=False)
    sched.finish("job-2", uploaded=False)
    app.state.poller.poll_once()
    assert bob.get(f"/api/artifacts/{priv['id']}").status_code == 404
    assert bob.get(f"/api/artifacts/{priv['id']}/image").status_code == 404
    assert bob.get(f"/api/artifacts/{shared['id']}/image").status_code == 200
    listed = bob.get(f"/api/artifacts?project_id={pub['id']}").json()["artifacts"]
    assert [a["id"] for a in listed] == [shared["id"]]
    assert bob.delete(f"/api/artifacts/{shared['id']}", headers=H).status_code == 403
    assert alice.delete(f"/api/artifacts/{shared['id']}", headers=H).status_code == 200
    assert bob.get(f"/api/artifacts/{shared['id']}").status_code == 404


def test_listing_pages_newest_first(alice):
    ids = [gen(alice).json()["artifacts"][0]["id"] for _ in range(3)]
    page = alice.get("/api/artifacts?limit=2").json()
    assert [a["id"] for a in page["artifacts"]] == ids[:0:-1] and page["next"].endswith("+00:00")
    rest = alice.get("/api/artifacts", params={"limit": 2, "before": page["next"]}).json()
    assert [a["id"] for a in rest["artifacts"]] == ids[:1] and rest["next"] is None
