
import os
import importlib.util
for root, _, files in os.walk("tests/unit"):
    for f in sorted(files):
        if f.startswith("test_") and f.endswith(".py"):
            path = os.path.join(root, f)
            print(f"Importing {path}", flush=True)
            try:
                spec = importlib.util.spec_from_file_location("m", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                print(f"Success {path}", flush=True)
            except Exception as e:
                pass

