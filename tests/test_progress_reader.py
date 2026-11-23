"""BG-05 readonly incremental stream tests. No subprocesses or network."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from benchagent.common import ROOT,sha
from benchagent.progress_reader import read_frames,frame_snapshot,read_log,READ_BYTES,DISPLAY_CHARS

HEADER=b"frame,step_ms,newton_iterations,pcg_iterations,pcg_solves\n"
def row(n):return (str(n)+",10,2,7,2\n").encode()

class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.root=Path(self.tmp.name);self.path=self.root/"frames.csv"
        self.binding={"job_id":"job-a","case_id":"smoke-20"}
    def tearDown(self):self.tmp.cleanup()
    def frames(self,cursor=None,**kw):
        return read_frames(self.path,binding=self.binding,target=20,cursor=cursor,**kw)
    def test_missing_source_does_not_create_files(self):
        state=self.frames()
        self.assertEqual(state["frames"],[]);self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.iterdir()),[])
    def test_header_half_line_and_continuation_are_not_double_counted(self):
        self.path.write_bytes(HEADER+row(1)+b"2,10,2")
        first=self.frames()
        self.assertEqual(len(first["frames"]),1)
        self.assertEqual(first["offset"],len(HEADER+row(1)))
        with self.path.open("ab") as f:f.write(b",7,2\n"+row(3))
        second=self.frames(first);third=self.frames(second)
        self.assertEqual([r["frame"] for r in second["frames"]],[1,2,3])
        self.assertEqual(second,third)
        self.assertEqual(len(first["frames"]),1)
    def test_custom_frame_count_can_resume_and_finish(self):
        self.path.write_bytes(HEADER + b"".join(row(n) for n in range(1, 15)))
        first = read_frames(self.path, binding=self.binding, target=29)
        self.assertEqual(frame_snapshot(first, 29, phase="RUNNING")["confirmed_frames"], 14)
        with self.path.open("ab") as stream:
            stream.write(b"".join(row(n) for n in range(15, 30)))
        finished = read_frames(self.path, binding=self.binding, target=29, cursor=first, finished=True)
        snapshot = frame_snapshot(finished, 29, phase="COMPLETED")
        self.assertEqual((snapshot["confirmed_frames"], snapshot["fraction"]), (29, 1.0))
        self.assertEqual(snapshot["stream_state"], "COLLECTED")
        for invalid in (0, 1001, True, 29.0):
            with self.subTest(target=invalid), self.assertRaises(ValueError):
                read_frames(self.path, binding=self.binding, target=invalid)

    def test_partial_header_waits_for_newline(self):
        self.path.write_bytes(HEADER[:-1])
        state=self.frames()
        self.assertIsNone(state["header"]);self.assertEqual(state["offset"],0)
        with self.path.open("ab") as f:f.write(b"\n"+row(1))
        self.assertEqual(len(self.frames(state)["frames"]),1)
    def test_crlf_bom_and_missing_collision_are_supported(self):
        self.path.write_bytes(b"\xef\xbb\xbf"+HEADER.replace(b"\n",b"\r\n")+row(1).replace(b"\n",b"\r\n"))
        state=self.frames()
        self.assertEqual(state["frames"][0]["collision_pair_count"],None)
        self.assertEqual(state["frames"][0]["pcg_iterations"],7)
    def test_duplicate_gap_backward_and_over_target_stop_at_valid_prefix(self):
        for suffix in (row(1),row(3),row(0),row(21)):
            self.path.write_bytes(HEADER+row(1)+suffix)
            state=self.frames()
            self.assertEqual(len(state["frames"]),1);self.assertTrue(state["blocked"])
            self.assertEqual(state["issues"][0]["code"],"FRAME_SEQUENCE")
            self.assertEqual(self.frames(state),state)
    def test_invalid_numbers_and_column_count_are_visible(self):
        for value in (b"1,nan,2,7,2\n",b"1,inf,2,7,2\n",b"1,-1,2,7,2\n",
                      b"1,10,true,7,2\n",b"1,10,2.0,7,2\n",b"1,10,2,7\n",
                      b"1,10,9999999999999999999999,7,2\n"):
            self.path.write_bytes(HEADER+value)
            state=self.frames()
            self.assertTrue(state["blocked"]);self.assertFalse(state["frames"])
            self.assertEqual(state["issues"][0]["code"],"INVALID_CSV")
    def test_bad_header_duplicate_or_missing_columns(self):
        for header in (b"frame,frame,step_ms\n",b"frame,step_ms\n",b"\n"):
            self.path.write_bytes(header)
            self.assertTrue(self.frames()["blocked"])
    def test_truncated_and_same_length_rewritten_prefix_block(self):
        self.path.write_bytes(HEADER+row(1))
        cursor=self.frames()
        self.path.write_bytes(HEADER)
        self.assertEqual(self.frames(cursor)["issues"][-1]["code"],"SOURCE_TRUNCATED")
        self.path.write_bytes(HEADER+row(1).replace(b",10,",b",11,"))
        state=self.frames(cursor)
        self.assertEqual(state["issues"][-1]["code"],"PREFIX_CHANGED")
        self.assertEqual(state["frames"][0]["solver_time_ms"],10)
    def test_replaced_or_disappeared_source_blocks_after_observation(self):
        self.path.write_bytes(HEADER+row(1));cursor=self.frames()
        moved=self.root/"old.csv";self.path.rename(moved)
        self.assertEqual(self.frames(cursor)["issues"][-1]["code"],"SOURCE_MISSING")
        self.path.write_bytes(HEADER+row(1))
        self.assertEqual(self.frames(cursor)["issues"][-1]["code"],"SOURCE_REPLACED")
    def test_cursor_cannot_cross_job_case_target_or_stream(self):
        self.path.write_bytes(HEADER+row(1));cursor=self.frames()
        for binding,target in (({**self.binding,"job_id":"job-b"},20),({**self.binding,"case_id":"other"},20),(self.binding,50)):
            with self.assertRaises(ValueError):
                read_frames(self.path,binding=binding,target=target,cursor=cursor)
        with self.assertRaises(ValueError):read_log(self.path,binding=self.binding,cursor=cursor)
    def test_final_partial_row_and_missing_csv_are_reported_not_counted(self):
        self.path.write_bytes(HEADER+row(1)+b"2,10")
        state=self.frames(finished=True)
        self.assertEqual(len(state["frames"]),1)
        self.assertIn("TRAILING_PARTIAL_ROW",[v["code"] for v in state["issues"]])
        missing=read_frames(self.root/"missing",binding=self.binding,target=20,finished=True)
        self.assertEqual(missing["issues"][0]["code"],"MISSING_CSV")
    def test_collected_target_does_not_produce_verdict_or_final_state(self):
        self.path.write_bytes(HEADER+b"".join(row(i) for i in range(1,21)))
        state=self.frames()
        result=frame_snapshot(state,20,phase="VALIDATING")
        self.assertEqual(result["fraction"],1)
        self.assertEqual(result["stream_state"],"COLLECTED")
        self.assertNotIn("result_status",result)
        self.assertIsNone(result["eta_seconds"])
    def test_eta_requires_enough_samples_and_running_phase(self):
        self.path.write_bytes(HEADER+b"".join(row(i) for i in range(1,6)))
        state=self.frames()
        self.assertAlmostEqual(frame_snapshot(state,20,phase="RUNNING")["eta_seconds"],.15)
        self.assertIsNone(frame_snapshot(state,20,phase="CANCELLED")["eta_seconds"])
        state["frames"]=state["frames"][:4]
        self.assertIsNone(frame_snapshot(state,20,phase="RUNNING")["eta_seconds"])
    def test_commit_race_rolls_back_new_observations(self):
        self.path.write_bytes(HEADER+row(1))
        with patch("benchagent.progress_reader._commit",side_effect=OSError("race")):
            state=self.frames()
        self.assertEqual(state["offset"],0);self.assertEqual(state["frames"],[])
        self.assertTrue(state["blocked"])
    def test_overlong_row_is_bounded(self):
        self.path.write_bytes(b"x"*READ_BYTES)
        self.assertEqual(self.frames()["issues"][0]["code"],"ROW_TOO_LONG")
    def test_read_only_and_replays_do_not_mutate_source(self):
        self.path.write_bytes(HEADER+row(1))
        before=sha(self.path)
        with patch("subprocess.Popen",side_effect=AssertionError("process")),patch("socket.create_connection",side_effect=AssertionError("network")):
            state=self.frames();self.frames(state)
        self.assertEqual(sha(self.path),before)

class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests");self.root=Path(self.tmp.name)
        self.path=self.root/"stdout.log";self.binding={"job_id":"job-a","case_id":"smoke-20","stream":"stdout"}
    def tearDown(self):self.tmp.cleanup()
    def log(self,cursor=None,**kw):return read_log(self.path,binding=self.binding,cursor=cursor,**kw)
    def test_utf8_split_and_no_newline_preserve_text_exactly(self):
        data="开始第一帧".encode()
        self.path.write_bytes(data[:5]);first=self.log()
        self.assertEqual(first["text"],"开");self.assertEqual(first["offset"],3)
        with self.path.open("ab") as f:f.write(data[5:])
        second=self.log(first);third=self.log(second)
        self.assertEqual(second["text"],"开始第一帧");self.assertEqual(second,third)
        self.assertEqual(second["offset"],len(data))
    def test_pause_does_not_advance_and_resume_reads_backlog(self):
        self.path.write_bytes("甲".encode());first=self.log()
        with self.path.open("ab") as f:f.write("乙".encode())
        paused=self.log(first,follow=False)
        self.assertEqual(paused,first)
        resumed=self.log(paused)
        self.assertEqual(resumed["text"],"甲乙")
    def test_stream_and_case_cursors_are_independent(self):
        self.path.write_bytes(b"stdout");cursor=self.log()
        for binding in ({**self.binding,"stream":"stderr"},{**self.binding,"case_id":"other"},{**self.binding,"job_id":"other"}):
            with self.assertRaises(ValueError):read_log(self.path,binding=binding,cursor=cursor)
    def test_invalid_utf8_and_final_incomplete_bytes_are_visible(self):
        self.path.write_bytes(b"ok\xff\xe4")
        state=self.log(finished=True)
        self.assertEqual(state["offset"],4)
        self.assertIn("INVALID_UTF8",[v["code"] for v in state["issues"]])
        self.assertEqual(state["text"],"ok\ufffd\ufffd")
    def test_backlog_consumes_in_bounded_chunks_without_duplicates(self):
        content=b"a"*(READ_BYTES+50)
        self.path.write_bytes(content);first=self.log()
        self.assertEqual(first["offset"],READ_BYTES);self.assertFalse(first["at_eof"])
        second=self.log(first)
        self.assertEqual(second["offset"],len(content));self.assertTrue(second["at_eof"])
        self.assertEqual(len(second["text"]),DISPLAY_CHARS);self.assertTrue(second["display_truncated"])
    def test_truncation_and_prefix_change_preserve_prior_text(self):
        self.path.write_bytes(b"first");first=self.log()
        self.path.write_bytes(b"other")
        changed=self.log(first)
        self.assertEqual(changed["text"],"first");self.assertTrue(changed["blocked"])
        self.path.write_bytes(b"x")
        self.assertEqual(self.log(first)["issues"][-1]["code"],"SOURCE_TRUNCATED")
    def test_log_race_rolls_back_text_and_offset(self):
        self.path.write_bytes(b"hello")
        with patch("benchagent.progress_reader._commit",side_effect=OSError("race")):state=self.log()
        self.assertEqual(state["text"],"");self.assertEqual(state["offset"],0)
    def test_missing_file_waits_without_creation(self):
        state=self.log()
        self.assertEqual(state["text"],"");self.assertFalse(self.path.exists())
