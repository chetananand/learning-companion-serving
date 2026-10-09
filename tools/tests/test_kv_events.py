"""Test the KV event counter with a real ZMQ publisher that sends frames in the vLLM v0.30.0 format."""

import threading
import time

import msgpack
import zmq

from tools import kv_events


def frame(topic: str, seq: int, events: list) -> list[bytes]:
    return [topic.encode(), seq.to_bytes(8, "big"), msgpack.packb([time.time(), events], use_bin_type=True)]


def test_decode_the_vllm_format():
    stored = {"type": "BlockStored", "block_hashes": [1, 2, 3], "parent_block_hash": None,
              "token_ids": list(range(192)), "block_size": 64, "lora_id": None, "medium": "GPU", "lora_name": None}
    topic, seq, events = kv_events.decode(frame("kv@10.0.0.1:8000@m", 7, [stored]))
    assert (topic, seq) == ("kv@10.0.0.1:8000@m", 7)
    assert (events[0]["type"], len(events[0]["block_hashes"])) == ("BlockStored", 3)


def test_tally_counts_evictions_by_medium_and_gaps():
    t = kv_events.Tally()
    t.add("a", 1, [{"type": "BlockStored", "block_hashes": [1, 2], "medium": "GPU"}], 100.0)
    t.add("a", 2, [{"type": "BlockRemoved", "block_hashes": [1], "medium": "GPU"},
                   {"type": "BlockStored", "block_hashes": [9], "medium": "CPU"}], 100.5)
    t.add("a", 5, [{"type": "AllBlocksCleared"}], 101.0)
    s = t.summary()
    assert s["evicted_gpu_blocks"] == 1
    assert s["by_endpoint"]["a"] == {"batches": 3, "stored": {"GPU": 2, "CPU": 1}, "removed": {"GPU": 1},
                                     "cleared": 1, "missed_batches": 2}
    assert s["per_second"]["100"] == {"stored_GPU": 2, "removed_GPU": 1, "stored_CPU": 1}


def test_listen_to_a_real_publisher():
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    port = pub.bind_to_random_port("tcp://127.0.0.1")

    def send() -> None:
        time.sleep(0.4)  # let the subscriber connect (the slow-joiner case of PUB/SUB)
        for i in range(5):
            pub.send_multipart(frame("kv@pod@m", i, [{"type": "BlockRemoved", "block_hashes": [i], "medium": "GPU"}]))
            time.sleep(0.02)

    threading.Thread(target=send, daemon=True).start()
    tally = kv_events.listen([f"tcp://127.0.0.1:{port}"], seconds=1.2, topic_filter="kv@")
    pub.close(linger=0)
    assert tally.summary()["evicted_gpu_blocks"] == 5
