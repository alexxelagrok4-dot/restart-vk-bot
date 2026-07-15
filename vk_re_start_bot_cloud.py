from __future__ import annotations

import json
import os
import random
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime

import requests


API_VERSION = os.getenv("VK_API_VERSION", "5.199")
TOKEN = os.getenv("VK_ACCESS_TOKEN", "").strip()
GROUP_ID = int(os.getenv("VK_GROUP_ID", "0") or "0")
ADMIN_PEER_ID = int(os.getenv("ADMIN_PEER_ID", "0") or "0")

STATE_FILE = os.getenv("STATE_FILE", "state.json")
LEADS_FILE = os.getenv("LEADS_FILE", "leads.jsonl")

if not TOKEN:
    raise RuntimeError("VK_ACCESS_TOKEN is required")
if not GROUP_ID:
    raise RuntimeError("VK_GROUP_ID is required")


def log(message: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def vk_call(method: str, params: dict | None = None) -> dict:
    payload = dict(params or {})
    payload["access_token"] = TOKEN
    payload["v"] = API_VERSION
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request("https://api.vk.com/method/" + method, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if "error" in result:
        err = result["error"]
        raise RuntimeError(f"VK API {method}: {err.get('error_code')} {err.get('error_msg')}")
    return result["response"]


def keyboard(buttons: list[list[tuple[str, str]]], one_time: bool = False) -> str:
    return json.dumps(
        {
            "one_time": one_time,
            "inline": False,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": label,
                            "payload": json.dumps({"cmd": cmd}, ensure_ascii=False),
                        },
                        "color": "primary" if cmd in {"diagnostic", "start"} else "secondary",
                    }
                    for label, cmd in row
                ]
                for row in buttons
            ],
        },
        ensure_ascii=False,
    )


MAIN_KEYBOARD = keyboard(
    [
        [("Записаться на диагностику", "diagnostic")],
        [("Как всё работает", "how"), ("Форматы", "formats")],
        [("Стоимость", "price"), ("Адрес", "address")],
        [("Персонально", "personal"), ("Мини-группы", "groups")],
    ]
)


FAQ = {
    "how": (
        "Как работает re:старт:\n\n"
        "1. Сначала — диагностика: цель, самочувствие, ограничения, привычный ритм.\n"
        "2. Потом — понятный маршрут на 4 недели.\n"
        "3. Дальше — тренировки с контролем техники и прогресса.\n"
        "4. В конце этапа — корректировка маршрута.\n\n"
        "Вы задаёте цель — мы создаём маршрут."
    ),
    "formats": (
        "Форматы re:старт:\n\n"
        "• персональное сопровождение;\n"
        "• мини-группы;\n"
        "• комбинированный маршрут;\n"
        "• диагностика и подбор уровня нагрузки.\n\n"
        "Формат лучше выбирать после диагностики — так безопаснее и точнее."
    ),
    "price": (
        "Стоимость зависит от формата сопровождения и частоты занятий.\n\n"
        "Чтобы не предлагать абонемент вслепую, мы сначала проводим диагностику, "
        "а затем рекомендуем подходящий маршрут."
    ),
    "address": (
        "Адрес и удобное время визита уточнит администратор при записи.\n\n"
        "Напишите «Диагностика» — я соберу заявку."
    ),
    "groups": (
        "Мини-группы — это формат, где тренер успевает видеть каждого, "
        "контролировать технику и адаптировать упражнения под уровень."
    ),
    "personal": (
        "Персональный формат подходит, если нужна максимальная индивидуализация: "
        "конкретная цель, ограничения, приватность или быстрый старт."
    ),
}


@dataclass
class Lead:
    created_at: str
    peer_id: int
    name: str
    goal: str
    limits: str
    time: str
    phone: str
    source: str = "VK bot"


def normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def send(peer_id: int, text: str, kb: str | None = MAIN_KEYBOARD) -> None:
    params = {
        "peer_id": peer_id,
        "random_id": random.randint(1, 2_000_000_000),
        "message": text,
    }
    if kb:
        params["keyboard"] = kb
    vk_call("messages.send", params)


def notify_admin(lead: Lead) -> None:
    if not ADMIN_PEER_ID:
        return
    text = (
        "Новая заявка на диагностику re:старт\n\n"
        f"Имя: {lead.name}\n"
        f"Цель: {lead.goal}\n"
        f"Ограничения: {lead.limits}\n"
        f"Удобное время: {lead.time}\n"
        f"Телефон: {lead.phone}\n"
        f"Источник: {lead.source}\n"
        f"VK peer_id: {lead.peer_id}"
    )
    try:
        send(ADMIN_PEER_ID, text, kb=None)
    except Exception as exc:
        log(f"admin_notify_failed {type(exc).__name__}: {exc}")


def start_lead(peer_id: int, state: dict) -> None:
    state[str(peer_id)] = {"flow": "lead", "step": "name", "data": {}}
    save_state(state)
    send(
        peer_id,
        "Отлично, запишем вас на диагностику.\n\n"
        "Вопрос 1 из 5: как к вам обращаться?",
        kb=None,
    )


def handle_lead(peer_id: int, text: str, state: dict) -> None:
    user = state.get(str(peer_id), {})
    step = user.get("step")
    data = user.setdefault("data", {})

    if normalize(text) in {"отмена", "стоп", "меню"}:
        state.pop(str(peer_id), None)
        save_state(state)
        send(peer_id, "Ок, вернулись в меню. Чем помочь?")
        return

    if step == "name":
        data["name"] = text.strip()
        user["step"] = "goal"
        send(
            peer_id,
            "Вопрос 2 из 5: какая главная цель?\n\n"
            "Например: тонус, спина, вес, энергия, регулярность.",
            kb=None,
        )
    elif step == "goal":
        data["goal"] = text.strip()
        user["step"] = "limits"
        send(peer_id, "Вопрос 3 из 5: есть ли ограничения, боли или дискомфорт?", kb=None)
    elif step == "limits":
        data["limits"] = text.strip()
        user["step"] = "time"
        send(peer_id, "Вопрос 4 из 5: когда удобнее прийти — утро, день, вечер или выходные?", kb=None)
    elif step == "time":
        data["time"] = text.strip()
        user["step"] = "phone"
        send(peer_id, "Вопрос 5 из 5: оставьте телефон для подтверждения записи.", kb=None)
    elif step == "phone":
        data["phone"] = text.strip()
        lead = Lead(
            created_at=datetime.now().isoformat(timespec="seconds"),
            peer_id=peer_id,
            name=data.get("name", ""),
            goal=data.get("goal", ""),
            limits=data.get("limits", ""),
            time=data.get("time", ""),
            phone=data.get("phone", ""),
        )
        with open(LEADS_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(lead), ensure_ascii=False) + "\n")
        notify_admin(lead)
        state.pop(str(peer_id), None)
        save_state(state)
        send(
            peer_id,
            "Спасибо. Заявка на диагностику сохранена.\n\n"
            "Администратор свяжется с вами, уточнит удобное время и ответит на вопросы.",
        )
        log(f"lead_saved peer_id={peer_id} name={lead.name!r}")
    else:
        state.pop(str(peer_id), None)
        save_state(state)
        send(peer_id, "Сценарий сброшен. Напишите «Диагностика», чтобы начать заново.")


def handle_message(peer_id: int, text: str, state: dict) -> None:
    if state.get(str(peer_id), {}).get("flow") == "lead":
        handle_lead(peer_id, text, state)
        return

    normalized = normalize(text)
    if any(word in normalized for word in ["запис", "диагностик", "старт"]):
        start_lead(peer_id, state)
    elif any(word in normalized for word in ["как", "работ", "маршрут"]):
        send(peer_id, FAQ["how"])
    elif any(word in normalized for word in ["формат", "уров", "тариф"]):
        send(peer_id, FAQ["formats"])
    elif any(word in normalized for word in ["цен", "стоим", "сколько"]):
        send(peer_id, FAQ["price"])
    elif any(word in normalized for word in ["адрес", "где", "карта"]):
        send(peer_id, FAQ["address"])
    elif any(word in normalized for word in ["мини", "групп"]):
        send(peer_id, FAQ["groups"])
    elif any(word in normalized for word in ["персон", "индивиду"]):
        send(peer_id, FAQ["personal"])
    elif any(word in normalized for word in ["привет", "здрав", "меню", "начать"]):
        send(
            peer_id,
            "Здравствуйте. Это re:старт.\n\n"
            "Мы помогаем вернуться к движению, тонусу и здоровью через диагностику, "
            "понятный маршрут и сопровождение тренера.\n\n"
            "Выберите кнопку ниже или напишите «Диагностика».",
        )
    else:
        send(
            peer_id,
            "Я могу помочь с записью на диагностику и ответить на ключевые вопросы.\n\n"
            "Напишите «Диагностика», «Стоимость», «Форматы», «Мини-группы», "
            "«Персонально» или «Адрес».",
        )


def configure_long_poll() -> None:
    vk_call(
        "groups.setLongPollSettings",
        {
            "group_id": GROUP_ID,
            "enabled": 1,
            "message_new": 1,
            "api_version": API_VERSION,
        },
    )
    log("long_poll_settings=enabled")


def get_long_poll_server() -> tuple[str, str, str]:
    server = vk_call("groups.getLongPollServer", {"group_id": GROUP_ID})
    return server["server"], server["key"], server["ts"]


def main() -> None:
    configure_long_poll()
    state = load_state()
    server_url, key, ts = get_long_poll_server()
    log(f"bot_started group_id={GROUP_ID}")

    while True:
        try:
            response = requests.get(
                server_url,
                params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
                timeout=35,
            ).json()
            if "failed" in response:
                log(f"longpoll_failed={response}")
                server_url, key, ts = get_long_poll_server()
                continue
            ts = response["ts"]
            for update in response.get("updates", []):
                if update.get("type") != "message_new":
                    continue
                message = update.get("object", {}).get("message", {})
                peer_id = int(message.get("peer_id"))
                text = message.get("text", "")
                if not text:
                    continue
                log(f"message peer_id={peer_id} text={text[:80]!r}")
                handle_message(peer_id, text, state)
                state = load_state()
        except KeyboardInterrupt:
            log("bot_stopped_keyboard")
            return
        except Exception as exc:
            log(f"bot_error {type(exc).__name__}: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
