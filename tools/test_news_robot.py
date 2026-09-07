import importlib.util
import os
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("news_robot.py")
SPEC = importlib.util.spec_from_file_location("news_robot", MODULE_PATH)
ROBOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROBOT)


def fake_fetch(url, params=None, headers=None):
    assert headers == {"Authorization": "Bearer test-token"}
    if "/by/username/" in url:
        return {"data": {"id": "123", "username": "UGT_LAPALMA"}}
    return {
        "data": [{
            "id": "999",
            "text": "Aviso importante para la plantilla",
            "created_at": "2026-09-07T08:00:00Z",
            "attachments": {"media_keys": ["3_abc"]},
        }],
        "includes": {"media": [{
            "media_key": "3_abc",
            "type": "photo",
            "url": "https://pbs.twimg.com/media/example.jpg",
            "alt_text": "Cartel sindical",
        }]},
    }


os.environ["X_BEARER_TOKEN"] = "test-token"
ROBOT.fetch_json_with_headers = fake_fetch
source = {
    "id": "ugt-granada-la-palma-x",
    "name": "UGT Granada La Palma",
    "type": "x_user_posts",
    "username": "UGT_LAPALMA",
    "accessTokenEnv": "X_BEARER_TOKEN",
    "limit": 10,
}
items, status = ROBOT.parse_x_user_posts(source)
assert status["ok"] is True
assert items[0]["id"] == "x-999"
assert items[0]["imageUrl"] == "https://pbs.twimg.com/media/example.jpg"
assert items[0]["url"] == "https://x.com/UGT_LAPALMA/status/999"
assert items[0]["publishedAt"] == "2026-09-07T08:00:00Z"

old = [{
    "id": "x-old",
    "sourceId": source["id"],
    "source": source["name"],
    "title": "Antigua",
    "summary": "Granada La Palma",
    "url": "https://x.com/UGT_LAPALMA/status/1",
    "publishedAt": "2026-01-01T00:00:00Z",
}]
merged = ROBOT.merge_items(old, items, {source["id"]})
assert len(merged) == 1
assert merged[0]["id"] == "x-999"
print("OK: X, imagen y reemplazo de publicaciones validados")
