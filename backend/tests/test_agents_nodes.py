from pathlib import Path
import sys


CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.nodes.spot_search import is_relevant_spot_place  # noqa: E402


def test_spot_search_filters_non_tourism_places() -> None:
    """测试景点检索会过滤学校、机关等非游玩 POI。"""
    assert not is_relevant_spot_place({"name": "大理市公安局", "type": "政府机构及社会团体"})
    assert not is_relevant_spot_place({"name": "大理市大庄完小", "type": "科教文化服务;学校"})
    assert not is_relevant_spot_place({"name": "花与菌野生菌火锅(大理古城人民路店)", "type": "餐饮服务"})
    assert is_relevant_spot_place({"name": "崇圣寺三塔文化旅游区", "type": "风景名胜;风景名胜相关"})
