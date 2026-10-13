import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dmc-tests-"))
os.environ["DATA_DIR"] = str(_tmp)
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["LLM_MOCK"] = "1"
os.environ["SIM_NOW"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
