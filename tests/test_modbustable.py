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
    assert batches == [[1, 2], [3, 1], [10, 2]]


def test_generate_batched_addresses_max_batch():
    table = ModbusTable(4)
    for addr in range(1, 6):
        table.add_register(addr)
    batches = table.get_batched_addresses()
    # Should batch: [1,2,3,4] (start=1, len=4), [5] (start=5, len=1)
    assert batches == [[1, 4], [5, 1]]


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
    assert batches == [[2, 1], [4, 1]]
