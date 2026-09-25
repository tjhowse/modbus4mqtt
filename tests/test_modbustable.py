import pytest
from modbus4mqtt.modbus_table import ModbusTable


def test_add_and_get_register():
    table = ModbusTable()
    table.add_register(10)
    assert table.get_value(10) == 0


def test_set_and_get_value():
    table = ModbusTable()
    table.add_register(5)
    table.set_value(5, 12345)
    assert table.get_value(5) == 12345


def test_set_value_with_mask():
    table = ModbusTable()
    table.add_register(7)
    table.set_value(7, 0xFFFF, mask=0x00FF)
    assert table.get_value(7) == 0x00FF


def test_set_value_out_of_range():
    table = ModbusTable()
    table.add_register(1)
    with pytest.raises(ValueError):
        table.set_value(1, -1)
    with pytest.raises(ValueError):
        table.set_value(1, 0x1_0000)


def test_get_value_invalid_address():
    table = ModbusTable()
    with pytest.raises(ValueError):
        table.get_value(99)


def test_set_value_invalid_address():
    table = ModbusTable()
    with pytest.raises(ValueError):
        table.set_value(99, 123)


def test_sort_registers():
    table = ModbusTable()
    table.add_register(20)
    table.add_register(10)
    table.sort()
    assert list(table._registers.keys()) == [10, 20]


def test_generate_batched_addresses_simple():
    table = ModbusTable(2)
    for addr in [1, 2, 3, 10, 11]:
        table.add_register(addr)
    batches = table.get_batched_addresses()
    # Should batch: [1,2] (start=1, len=2), [3] (start=3, len=1), [10,11] (start=10, len=2)
    assert batches == [(1, 2), (3, 1), (10, 2)]


def test_generate_batched_addresses_max_batch():
    table = ModbusTable(4)
    for addr in range(1, 6):
        table.add_register(addr)
    batches = table.get_batched_addresses()
    # Should batch: [1,2,3,4] (start=1, len=4), [5] (start=5, len=1)
    assert batches == [(1, 4), (5, 1)]


def test_generate_batched_addresses_max_batch_write_mode():
    table = ModbusTable(4)
    for addr in range(1, 6):
        table.add_register(addr)
    batches = table.get_batched_addresses(write_mode=True)
    # No registers changed yet, so no batches
    assert batches == []
    for addr in [2, 4]:
        table.set_value(addr, 123, write=True)
    batches = table.get_batched_addresses(write_mode=True)
    # Should batch: [2] (start=2, len=1), [4] (start=4, len=1)
    assert batches == [(2, 1), (4, 1)]


def test_write_only_address_not_polled():
    # CG-style PLCs use disjoint read/write address spaces. A write to an
    # address that was never registered for monitoring (set_topic only, with
    # monitor: false) must be tracked for the next write flush but must NOT
    # be added to the polled read batches.
    table = ModbusTable(8)
    for addr in [1, 2, 3]:
        table.add_register(addr)
    # Simulate an MQTT-driven write to a non-monitored address.
    table.set_value(99, 1, write=True)
    read_batches = table.get_batched_addresses()
    write_batches = table.get_batched_addresses(write_mode=True)
    assert read_batches == [(1, 3)], "write-only address must not poison read batches"
    assert write_batches == [(99, 1)]


def test_write_before_first_read_does_not_poison_read_cache():
    # Regression: a retained MQTT /set message can arrive and flush through
    # _process_writes -> get_batched_addresses(write_mode=True) BEFORE the
    # first poll(). If the read/write distinction is collapsed in the cache,
    # the next poll() will issue Read Holding Registers against the
    # write-only address (12291 in the wild), which CG PLCs silently ignore
    # and never recover from.
    table = ModbusTable(8)
    for addr in [1, 2, 3]:
        table.add_register(addr)
    # First call out of __init__ is the write flush, not a read.
    table.set_value(12291, 1, write=True)
    write_batches = table.get_batched_addresses(write_mode=True)
    assert write_batches == [(12291, 1)]
    # Now the first poll asks for read batches. Must NOT include 12291.
    read_batches = table.get_batched_addresses()
    assert read_batches == [(1, 3)], (
        f"write-only address poisoned read batches: {read_batches}"
    )


def test_repeated_write_to_unpolled_register_is_flushed():
    # The bridge never reads an unpolled register back, so it cannot know the
    # write is redundant. A CG pulse register also acts on every write.
    table = ModbusTable(8)
    table.set_value(12289, 1, write=True)
    assert table.get_batched_addresses(write_mode=True) == [(12289, 1)]
    table.clear_changed_registers()
    table.set_value(12289, 1, write=True)
    assert table.get_batched_addresses(write_mode=True) == [(12289, 1)]


def test_write_matching_polled_value_is_skipped():
    table = ModbusTable(8)
    table.add_register(5)
    table.set_value(5, 7)
    table.set_value(5, 7, write=True)
    assert table.get_batched_addresses(write_mode=True) == []
    table.set_value(5, 8, write=True)
    assert table.get_batched_addresses(write_mode=True) == [(5, 1)]
