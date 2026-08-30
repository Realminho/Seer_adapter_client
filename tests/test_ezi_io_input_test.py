import importlib.util
from pathlib import Path


def load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "ezi_io_input_test.py"
    spec = importlib.util.spec_from_file_location("ezi_io_input_test", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_format_sensor_states_maps_slots_to_configured_pins():
    module = load_script_module()
    bits = [0] * 16
    bits[8] = 1
    bits[12] = 1

    assert module.format_sensor_states(bits, [8, 9, 10, 11, 12, 13]) == (
        "slot1=on slot2=off slot3=off slot4=off slot5=on slot6=off"
    )
