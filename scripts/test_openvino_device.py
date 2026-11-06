"""Compile and run a tiny OpenVINO graph on the selected device.

Usage:
  python scripts/test_openvino_device.py
  python scripts/test_openvino_device.py --device GPU

This checks actual graph compilation and inference, not just device enumeration.
"""
import argparse
import sys

import numpy as np
from openvino import Core, Model, opset13


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="GPU", help="OpenVINO device, e.g. GPU or CPU")
    args = parser.parse_args()

    core = Core()
    devices = core.available_devices
    print("Available devices:", devices)
    if args.device not in devices:
        print(f"ERROR: requested device {args.device!r} is unavailable.")
        return 2

    parameter = opset13.parameter([1, 3], np.float32, name="input")
    result = opset13.relu(parameter)
    model = Model([result], [parameter], "openvino_device_smoke_test")
    compiled = core.compile_model(model, args.device)
    sample = np.array([[-1.0, 2.0, -3.0]], dtype=np.float32)
    output = compiled({compiled.input(0): sample})
    values = next(iter(output.values()))

    print("Compiled device:", compiled.get_property("EXECUTION_DEVICES"))
    print("Input:", sample.tolist())
    print("Output:", values.tolist())
    expected = np.array([[0.0, 2.0, 0.0]], dtype=np.float32)
    if not np.allclose(values, expected):
        print("ERROR: inference output did not match expected ReLU result.")
        return 3
    print("SUCCESS: OpenVINO compiled and executed a graph on", args.device)
    return 0


if __name__ == "__main__":
    sys.exit(main())
