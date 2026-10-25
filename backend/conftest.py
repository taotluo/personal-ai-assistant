import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 测试期间把全局 settings 的数据/产物目录指到临时目录，
# 避免 coder/research 等使用全局 settings 的组件污染 backend/data。
os.environ.setdefault("PAA_DATA_DIR", str(Path(tempfile.gettempdir()) / "paa-test-data"))
