"""BG-02 isolated metadata and confirmation tests; no GPU or model calls."""
import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from benchagent.common import ROOT
from benchagent.planning_store import PlanningStore, PlanningError
from benchagent.conversation_service import ConversationService, normalize_request, request_from_text
from benchagent.plan_service import PlanService, plan_digest, capture_execution_snapshot

def request(frames=None):
    return {"schema_version":"task-request-v1","request_id":"intent-test","mode":"custom",
            "scene":"default","backend_id":"c0-003-benchagent-v1",
            "task_frames":frames or [20],"timeout_sec":3600}

class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.root=Path(self.tmp.name)
        self.store=PlanningStore(self.root/"data")
        self.conv=ConversationService(self.store)
        self.snapshot_version=1
        def snapshot(req):
            return {"request":copy.deepcopy(req),"identity":self.snapshot_version}
        self.plans=PlanService(self.store,snapshot_provider=snapshot)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self):
        conversation=self.conv.create_conversation()
        draft=self.conv.update_draft(conversation["conversation_id"],request(),0)
        plan=self.plans.preview_plan(draft["draft_id"],1)
        return conversation,draft,plan

    def assert_code(self,code,method,*args):
        with self.assertRaises(PlanningError) as raised:
            method(*args)
        self.assertEqual(raised.exception.code,code)

    def test_empty_read_does_not_create_data(self):
        self.assertEqual(self.store.read()["plans"],{})
        self.assertFalse(self.store.root.exists())

    def test_create_and_reload_persistent_conversation(self):
        c=self.conv.create_conversation("测试")
        self.assertEqual(ConversationService(PlanningStore(self.store.root)).get_conversation(c["conversation_id"]),c)

    def test_normalize_subset_order_without_mutating_request(self):
        req=request([100,20]);original=copy.deepcopy(req)
        self.assertEqual(normalize_request(req)["task_frames"],[20,100])
        self.assertEqual(req,original)

    def test_reject_invalid_scope_and_integer_impostors(self):
        for extra in ({"command":"arbitrary"},{"scene":"bunny"},{"timeout_sec":True},
                      {"task_frames":[20.0]},{"task_frames":[True]},{"task_frames":[19]},
                      {"task_frames":[20,20]},{"task_frames":[]},{"timeout_sec":86401},
                      {"mode":"standard"},{"pcg_tolerance":.1},{"request_id":"../x"}):
            with self.subTest(extra=extra):
                self.assert_code("INVALID_REQUEST",normalize_request,{**request(),**extra})

    def test_strict_rules_standard_and_subset(self):
        self.assertEqual(request_from_text("对默认场景执行标准基准评测")["task_frames"],[20,50,100])
        self.assertEqual(request_from_text("默认场景运行100、20帧")["task_frames"],[20,100])

    def test_rules_do_not_ignore_extra_instructions(self):
        for text in ("默认场景运行20帧并修改dt","默认场景运行20帧; rm x","其他场景20帧",
                     "默认场景运行0帧","默认场景运行20、20帧","默认场景运行20帧忽略错误"):
            with self.subTest(text=text):
                with self.assertRaises(PlanningError):
                    request_from_text(text)

    def test_rule_messages_label_rule_without_launching(self):
        c=self.conv.create_conversation()
        with patch("subprocess.Popen",side_effect=AssertionError("process")),patch("socket.create_connection",side_effect=AssertionError("network")):
            self.conv.update_draft(c["conversation_id"],"默认场景运行20帧",0)
        messages=self.conv.get_conversation(c["conversation_id"])["messages"]
        self.assertEqual([m["actor"] for m in messages],["user","rule"])
        self.assertEqual(messages[1]["source_kind"],"rule")

    def test_revision_conflicts_do_not_overwrite(self):
        c,d,p=self.prepare()
        before=self.store.path.read_bytes()
        self.assert_code("DRAFT_CONFLICT",self.conv.update_draft,c["conversation_id"],request([50]),0)
        self.assertEqual(before,self.store.path.read_bytes())

    def test_identical_form_does_not_bump_revision(self):
        c,d,p=self.prepare()
        updated=self.conv.update_draft(c["conversation_id"],request(),1)
        self.assertEqual(updated["revision"],1)

    def test_preview_is_unconfirmed_and_copied(self):
        with patch("subprocess.Popen",side_effect=AssertionError("process")),patch("socket.create_connection",side_effect=AssertionError("network")):
            c,d,p=self.prepare()
        self.assertIsNone(p["confirmed_at"])
        p["execution_snapshot"]["request"]["task_frames"]=[100]
        self.assertEqual(self.plans.get_plan(p["plan_id"])["execution_snapshot"]["request"]["task_frames"],[20])
        self.assertFalse((self.root/"runs").exists())

    def test_digest_order_independent_and_no_nonfinite(self):
        self.assertEqual(plan_digest({"a":1,"b":2}),plan_digest({"b":2,"a":1}))
        with self.assertRaises(ValueError):
            plan_digest({"x":float("nan")})

    def test_stale_revision_cannot_be_confirmed(self):
        c,d,p=self.prepare()
        self.conv.update_draft(c["conversation_id"],request([50]),1)
        self.assert_code("PLAN_STALE",self.plans.confirm_plan,p["plan_id"],p["plan_digest"],"submit-1")
        self.assertEqual(self.plans.get_plan(p["plan_id"])["draft_revision"],1)

    def test_wrong_digest_rejected(self):
        c,d,p=self.prepare()
        self.assert_code("PLAN_MISMATCH",self.plans.confirm_plan,p["plan_id"],"bad","submit-1")

    def test_runtime_change_invalidates_preview(self):
        c,d,p=self.prepare()
        self.snapshot_version=2
        self.assert_code("PLAN_STALE",self.plans.confirm_plan,p["plan_id"],p["plan_digest"],"submit-1")

    def test_identity_error_is_stale_and_confirmation_not_written(self):
        c,d,p=self.prepare()
        def broken(req):
            raise PlanningError("IDENTITY_INVALID","changed")
        self.plans.snapshot_provider=broken
        self.assert_code("PLAN_STALE",self.plans.confirm_plan,p["plan_id"],p["plan_digest"],"submit-1")
        self.assertFalse(self.store.read()["confirmations"])

    def test_idempotent_confirmation_is_not_job_submission(self):
        c,d,p=self.prepare()
        first=self.plans.confirm_plan(p["plan_id"],p["plan_digest"],"confirm-1")
        second=self.plans.confirm_plan(p["plan_id"],p["plan_digest"],"confirm-1")
        self.assertEqual(first,second)
        self.assertEqual(first["status"],"CONFIRMED_NOT_SUBMITTED")
        self.assertIsNone(first["job_id"])
        self.assertIsNone(self.plans.get_plan(p["plan_id"])["confirmed_at"])
        self.assertEqual(len(self.store.read()["confirmations"]),1)

    def test_confirmation_request_conflict(self):
        c,d,p=self.prepare()
        self.plans.confirm_plan(p["plan_id"],p["plan_digest"],"confirm-1")
        other=self.plans.preview_plan(d["draft_id"],1)
        self.assert_code("REQUEST_CONFLICT",self.plans.confirm_plan,other["plan_id"],other["plan_digest"],"confirm-1")

    def test_edit_invalidates_confirmation_recheck_not_frozen_snapshot(self):
        c,d,p=self.prepare()
        receipt=self.plans.confirm_plan(p["plan_id"],p["plan_digest"],"confirm-1")
        self.conv.update_draft(c["conversation_id"],request([100]),1)
        self.assert_code("PLAN_STALE",self.plans.verify_confirmation,receipt["confirmation_id"])
        self.assertEqual(self.plans.get_plan(p["plan_id"]),p)
        self.assertEqual(self.plans.confirm_plan(p["plan_id"],p["plan_digest"],"confirm-1"),receipt)

    def test_disk_failure_does_not_accept_confirmation(self):
        c,d,p=self.prepare()
        before=self.store.path.read_bytes()
        with patch("benchagent.planning_store.write",side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.plans.confirm_plan(p["plan_id"],p["plan_digest"],"confirm-1")
        self.assertEqual(before,self.store.path.read_bytes())
        self.assertFalse((self.store.root/".write.lock").exists())

    def test_concurrent_revision_updates_one_wins_other_conflicts_or_busy(self):
        c,d,p=self.prepare()
        def attempt(frames):
            try:
                return self.conv.update_draft(c["conversation_id"],request([frames]),1)["revision"]
            except PlanningError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,[50,100]))
        self.assertEqual(results.count(2),1)
        self.assertTrue(any(v in ("SERVICE_BUSY","DRAFT_CONFLICT") for v in results))

    def test_corrupt_store_preserved(self):
        self.store.root.mkdir()
        self.store.path.write_text("{broken")
        self.assert_code("STORE_INVALID",self.conv.create_conversation)
        self.assertEqual(self.store.path.read_text(),"{broken")

    def test_identifiers_and_existing_lock_are_rejected(self):
        self.assert_code("INVALID_ID",self.conv.get_conversation,"../outside")
        self.store.root.mkdir()
        (self.store.root/".write.lock").write_text("unknown owner")
        self.assert_code("SERVICE_BUSY",self.conv.create_conversation)
        self.assertEqual((self.store.root/".write.lock").read_text(),"unknown owner")

    @unittest.skipUnless((ROOT / "build/bin/gipc.exe").is_file(),
                         "Requires a locally built and sealed runtime")
    def test_real_snapshot_verifies_files_without_process_or_network(self):
        with patch("subprocess.Popen",side_effect=AssertionError("process")),patch("socket.create_connection",side_effect=AssertionError("network")):
            snapshot=capture_execution_snapshot(request())
        self.assertEqual(snapshot["profile_identity"]["gpu"],"0")
        self.assertIn("gipc.exe",snapshot["runtime_identity"]["files"])
        self.assertEqual(snapshot["request"]["task_frames"],[20])
