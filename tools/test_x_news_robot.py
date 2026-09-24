import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("x_news_robot.py")
SPEC = importlib.util.spec_from_file_location("x_news_robot", MODULE_PATH)
ROBOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROBOT)


def entry(post_id, created_ms, text, image):
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return (
        f'rest_id:"{post_id}",result:$R[1]={{core:{{screen_name:"UGT_LAPALMA"}},details:$R[2]={{created_at_ms:{created_ms},'
        f'full_text:"{escaped}"}},media_entities2:$R[3]=['
        f'{{media_url_https:"{image}"}}]}}}},entry_id:"tweet-{post_id}"'
    )


fixture = "profile_user_originals_timeline" + "".join([
    entry("105", 1789753543000, "https://t.co/abc", "https://pbs.twimg.com/media/new.jpg"),
    entry("104", 1788426664000, "EXCESO DE JORNADA 2025.\nSegundo parrafo https://t.co/def", "https://pbs.twimg.com/media/a.jpg"),
    entry("103", 1787000000000, "COMUNICADO https://t.co/ghi", "https://pbs.twimg.com/media/b.png"),
    entry("102", 1786000000000, "Aviso para la plantilla", "https://pbs.twimg.com/media/c.jpg"),
    entry("101", 1785000000000, "Publicacion antigua", "https://pbs.twimg.com/media/d.jpg"),
])

posts = ROBOT.extract_original_posts(fixture)
assert [post["postId"] for post in posts] == ["105", "104", "103", "102"]
assert posts[0]["text"] == ""
assert posts[1]["text"] == "EXCESO DE JORNADA 2025.\nSegundo parrafo"
assert posts[2]["imageUrl"] == "https://pbs.twimg.com/media/b.png?format=png&name=medium"

assert ROBOT.clean_ocr_text(" UGT  \n\nEXCESO  DE JORNADA\nEXCESO  DE JORNADA") == "UGT\nEXCESO DE JORNADA"
assert ROBOT.first_useful_line("COMUNICADO", "EXCESO DE JORNADA\nMas informacion", posts[0]["publishedAt"]) == "EXCESO DE JORNADA"
assert ROBOT.combined_summary("COMUNICADO", "Texto del cartel") == "COMUNICADO Texto del cartel"

print("OK: cuatro posts originales, texto, imagen y OCR validados")
