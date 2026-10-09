

def test_an_env_entry_with_an_empty_value_is_recorded():
    """Session 2: Kubernetes drops an empty value (BARRIER_LMCACHE_METRICS=""), and the record step failed."""
    from tools.run_record import container_record
    c = {"name": "store-barrier", "env": [{"name": "A", "value": "x"}, {"name": "B"},
                                          {"name": "C", "valueFrom": {"fieldRef": {"fieldPath": "status.hostIP"}}}]}
    assert container_record(c, {})["env"] == {"A": "x", "B": "", "C": "<from fieldRef>"}
