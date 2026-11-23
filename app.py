import base64, json, re
from urllib.parse import quote_plus
import requests
import streamlit as st
from openai import OpenAI

st.set_page_config(page_title="Fridge Feast", page_icon="🍳", layout="wide")

# ---------- Sidebar: settings ----------
st.sidebar.title("Chef settings")
api_key = st.secrets.get("API_KEY", "")
model = st.sidebar.text_input("Model", value="gemini-2.5-flash",
                              help="Check Google AI Studio for current free models")
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

st.sidebar.subheader("Mission preferences")
diet = st.sidebar.selectbox("Diet", ["No restriction", "Vegetarian", "Vegan", "Eggetarian", "Non-veg only"])
people = st.sidebar.number_input("People", 1, 12, 2)
max_time = st.sidebar.slider("Max cooking time (min)", 10, 90, 30, step=5)
extra = st.sidebar.text_input("Other notes", placeholder="e.g. less spicy, use spinach first")

# ---------- Tools (plain Python functions) ----------
MEALDB = "https://www.themealdb.com/api/json/v1/1"

def search_recipes(ingredient: str):
    r = requests.get(f"{MEALDB}/filter.php", params={"i": ingredient.strip().replace(" ", "_")}, timeout=15)
    meals = (r.json() or {}).get("meals") or []
    return [{"id": m["idMeal"], "name": m["strMeal"]} for m in meals[:10]]

def _fetch(recipe_id: str):
    r = requests.get(f"{MEALDB}/lookup.php", params={"i": recipe_id}, timeout=15)
    meals = (r.json() or {}).get("meals")
    return meals[0] if meals else None

def get_recipe(recipe_id: str):
    m = _fetch(recipe_id)
    if not m:
        return {"error": "Recipe not found"}
    ings = []
    for i in range(1, 21):
        name = (m.get(f"strIngredient{i}") or "").strip()
        if name:
            ings.append(f"{(m.get(f'strMeasure{i}') or '').strip()} {name}".strip())
    recipe_name = m["strMeal"]
    youtube_url = (m.get("strYoutube") or "").strip()
    if not youtube_url:
        youtube_url = f"https://www.youtube.com/results?search_query={quote_plus(recipe_name + ' recipe')}"
    return {"id": m["idMeal"], "name": m["strMeal"], "cuisine": m["strArea"],
            "category": m["strCategory"], "ingredients": ings,
            "image_url": m.get("strMealThumb"),
            "youtube_url": youtube_url,
            "instructions": (m["strInstructions"] or "")[:1500]}

def make_grocery_list(recipe_id: str, have: list):
    m = _fetch(recipe_id)
    if not m:
        return {"error": "Recipe not found"}
    have_l = [h.lower().strip() for h in have]
    missing = []
    for i in range(1, 21):
        name = (m.get(f"strIngredient{i}") or "").strip()
        if not name:
            continue
        n = name.lower()
        if not any(h in n or n in h for h in have_l):
            missing.append(f"{(m.get(f'strMeasure{i}') or '').strip()} {name}".strip())
    return {"recipe": m["strMeal"], "missing": missing}

TOOL_FUNCS = {"search_recipes": search_recipes, "get_recipe": get_recipe,
              "make_grocery_list": make_grocery_list}

TOOLS = [
    {"type": "function", "function": {
        "name": "search_recipes",
        "description": "Find recipes that contain a single main ingredient. Returns id and name.",
        "parameters": {"type": "object",
                       "properties": {"ingredient": {"type": "string", "description": "e.g. chicken, spinach"}},
                       "required": ["ingredient"]}}},
    {"type": "function", "function": {
        "name": "get_recipe",
        "description": "Get full ingredients and instructions for a recipe id.",
        "parameters": {"type": "object",
                       "properties": {"recipe_id": {"type": "string"}},
                       "required": ["recipe_id"]}}},
    {"type": "function", "function": {
        "name": "make_grocery_list",
        "description": "Given a recipe id and what the user has, return the ingredients they still need to buy.",
        "parameters": {"type": "object",
                       "properties": {"recipe_id": {"type": "string"},
                                      "have": {"type": "array", "items": {"type": "string"}}},
                       "required": ["recipe_id", "have"]}}},
]

# ---------- LLM helpers ----------
def get_client():
    return OpenAI(api_key=api_key, base_url=BASE_URL)

def api_error_message(error):
    status_code = getattr(error, "status_code", None)
    message = str(error)
    if status_code == 429 or "429" in message:
        return (
            "The Gemini API is temporarily rate-limited or has exhausted its quota "
            "(Error 429). Wait a minute, check your Gemini API usage and billing, "
            "or use a key with available quota."
        )
    if status_code in (401, 403) or "401" in message or "403" in message:
        return "The Gemini API key was rejected. Check the API_KEY value in Streamlit secrets."
    return f"The AI chef could not respond: {message}"

def extract_ingredients(image_bytes: bytes, mime: str):
    try:
        b64 = base64.b64encode(image_bytes).decode()
        r = get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": 'List every food ingredient visible in this fridge/pantry photo. '
                                         'Reply ONLY with JSON: {"ingredients": ["name", ...]}. Use simple common names.'},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
        )
        text = re.sub(r"```(?:json)?", "", r.choices[0].message.content).strip()
        return json.loads(text).get("ingredients", [])
    except Exception as error:
        raise RuntimeError(api_error_message(error)) from error

def system_prompt(ingredients):
    return f"""You are a practical home-cooking assistant.
The user has these ingredients: {', '.join(ingredients)}.
Preferences: diet={diet}; cooking for {people}; max {max_time} minutes; notes: {extra or 'none'}.

How to work:
1. Call search_recipes for 2-4 of the user's main ingredients (prefer ones they said to use first).
2. Pick the 2-3 most promising candidates and call get_recipe on them. Respect the diet.
3. Call make_grocery_list for your top pick(s), passing the user's ingredients as `have`.
4. Reply with: your top pick and why, the missing items to buy, short steps scaled for {people} people,
   and one backup option. Be concise and friendly.
5. For the top pick and backup, include the dish image using exact Markdown:
   `![Dish name](image_url)` and include a visible YouTube link using exact Markdown:
   `[Watch a recipe video](youtube_url)`. Use the image_url and youtube_url returned by get_recipe.
   Never invent recipe ids or media URLs. Never use HTML image tags.
TheMealDB is limited, so if nothing fits the diet or time, say so and suggest a simple dish from your own knowledge."""

def run_agent(messages, status):
    client = get_client()
    for _ in range(8):  # safety cap on loop iterations
        resp = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append(msg)
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            status.write(f"🔧 `{call.function.name}({', '.join(f'{k}={v}' for k, v in args.items())})`")
            try:
                result = TOOL_FUNCS[call.function.name](**args)
            except Exception as e:
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result)})
    return "I got stuck in a loop. Try rephrasing your request."

# ---------- UI ----------
st.markdown(
    """
    <style>
    [data-testid="stMainBlockContainer"] {
        background:
            radial-gradient(ellipse at 92% 4%, rgba(246, 174, 45, 0.16), transparent 28%),
            radial-gradient(ellipse at 4% 34%, rgba(230, 126, 34, 0.07), transparent 24%);
    }
    [data-testid="stMetric"] {
        background: linear-gradient(145deg, rgba(255, 255, 255, 0.92), rgba(255, 247, 232, 0.86));
        border-radius: 18px;
        box-shadow: 0 8px 24px rgba(129, 83, 35, 0.08);
        transition: transform 180ms ease, box-shadow 180ms ease;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-4px);
        box-shadow: 0 14px 30px rgba(129, 83, 35, 0.16);
    }
    [data-testid="stChatMessage"] {
        border-radius: 18px;
        transition: transform 180ms ease, box-shadow 180ms ease;
    }
    [data-testid="stChatMessage"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 22px rgba(129, 83, 35, 0.08);
    }
    .stButton button {
        transition: transform 160ms ease, box-shadow 160ms ease, filter 160ms ease;
    }
    .stButton button:hover {
        transform: translateY(-2px);
        box-shadow: 0 7px 16px rgba(180, 95, 25, 0.18);
        filter: saturate(1.08);
    }
    [data-testid="stFileUploader"] section {
        border: 1px dashed rgba(198, 112, 42, 0.45);
        border-radius: 16px;
        transition: border-color 180ms ease, background-color 180ms ease;
    }
    [data-testid="stFileUploader"] section:hover {
        border-color: #e67e22;
        background-color: rgba(230, 126, 34, 0.04);
    }
    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            scroll-behavior: auto !important;
            transition-duration: 0.01ms !important;
        }
    }
    .hero-copy {
        padding: 4px 2px 18px 2px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.container(horizontal=True, vertical_alignment="bottom"):
    with st.container():
        st.badge("Daily cooking quest", icon=":material/restaurant:", color="orange")
        st.title("Fridge Feast")
        st.caption("Turn what you already have into tonight's winning dish.")
    st.badge("Fresh mission", icon=":material/eco:", color="green")

if not api_key:
    st.error("Gemini API key is not configured. Add API_KEY to Streamlit secrets.")
    st.stop()

ss = st.session_state
ss.setdefault("ingredients", [])
ss.setdefault("messages", [])   # full LLM history (incl. tool msgs)
ss.setdefault("chat", [])       # what we show: (role, text)
ss.setdefault("xp", 0)
ss.setdefault("quests_completed", 0)
ss.setdefault("last_prompt", "")
ss.setdefault("quest_mode", "Use it up")

level = 1 + ss.xp // 100
level_progress = ss.xp % 100

with st.grid(3):
    st.metric("Ingredients found", len(ss.ingredients), "mission supplies", border=True)
    st.metric("Chef level", level, f"{100 - level_progress} XP to next level", border=True)
    st.metric("Quests completed", ss.quests_completed, "dinner wins", border=True)

st.caption(f"Level {level} progress")
st.progress(level_progress, text=f"{level_progress}/100 XP")

left, right = st.columns([0.9, 1.6], gap="large")

with left:
    with st.container(border=True):
        st.subheader("Step 1: Scout your fridge")
        st.caption("Upload a photo or type the ingredients you have.")

        st.caption("Quick add")
        quick_ingredients = st.pills(
            "Pantry favorites",
            ["tomato", "onion", "rice", "chicken", "spinach", "eggs", "potatoes", "cheese"],
            selection_mode="multi",
            label_visibility="collapsed",
        )
        if quick_ingredients:
            combined = sorted(set(
                ss.ingredients + [item.lower().strip() for item in quick_ingredients]
            ))
            if combined != ss.ingredients:
                ss.ingredients = combined
                st.rerun()

        photo = st.file_uploader(
            "Fridge photo",
            type=["jpg", "jpeg", "png"],
            label_visibility="collapsed",
        )
        scan_col, clear_col = st.columns([1.4, 1], vertical_alignment="bottom")
        with scan_col:
            scan_photo = st.button(
                "Scan fridge",
                icon=":material/photo_camera:",
                width="stretch",
                disabled=photo is None,
            )
        with clear_col:
            clear_ingredients = st.button(
                "Clear",
                icon=":material/delete:",
                width="stretch",
            )

        if scan_photo:
            with st.spinner("Spotting ingredients..."):
                try:
                    found = extract_ingredients(photo.getvalue(), photo.type)
                    ss.ingredients = sorted(set(ss.ingredients + found))
                    ss.xp += 10
                    st.success(f"Found {len(found)} ingredients! +10 XP")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        if clear_ingredients:
            ss.ingredients = []
            st.rerun()

        text = st.text_area(
            "Ingredients",
            value="\n".join(ss.ingredients),
            height=220,
            placeholder="spinach\nchicken\nrice",
        )
        ss.ingredients = [x.strip() for x in text.splitlines() if x.strip()]

        st.divider()
        st.caption("Today's mission")
        mission_a, mission_b = st.columns(2)
        mission_a.metric("Prep time", f"{max_time} min", border=True)
        mission_b.metric("Table", f"{people} people", border=True)

with right:
    with st.container(border=True):
        st.subheader("Step 2: Meet your dinner")
        st.caption("Choose a quest and let the chef build your plan.")

        ss.quest_mode = st.segmented_control(
            "Dinner quest",
            ["Use it up", "Quick meal", "Vegetarian", "High protein"],
            default=ss.quest_mode,
            label_visibility="collapsed",
        )

        quest_col, custom_col = st.columns([1.2, 1], vertical_alignment="bottom")
        with quest_col:
            quick = st.button(
                "Plan dinner",
                icon=":material/skillet:",
                type="primary",
                width="stretch",
            )
        with custom_col:
            surprise = st.button(
                "Surprise me",
                icon=":material/auto_awesome:",
                width="stretch",
            )

        user_msg = st.chat_input(
            "Ask for a vegetarian dish, less spicy, quick meals...",
            submit_mode="disable",
        )
        if surprise:
            user_msg = "Choose the most useful dinner recipe from my ingredients and surprise me with a delicious option."
        if quick:
            user_msg = "What can I cook for dinner tonight?"

        if ss.quest_mode == "Vegetarian":
            quest_instruction = "Make sure the recipe is vegetarian."
        elif ss.quest_mode == "Quick meal":
            quest_instruction = "Choose a recipe that takes 30 minutes or less."
        elif ss.quest_mode == "High protein":
            quest_instruction = "Prefer a high-protein dinner."
        else:
            quest_instruction = "Prioritize using the ingredients I already have."

        if user_msg:
            user_msg = f"{user_msg} {quest_instruction}"

    for role, content in ss.chat:
        with st.chat_message(role):
            st.markdown(content)

    if user_msg and user_msg != ss.last_prompt:
        ss.last_prompt = user_msg
        if not ss.ingredients:
            st.warning("Add some ingredients first.")
        else:
            ss.chat.append(("user", user_msg))
            with st.chat_message("user"):
                st.markdown(user_msg)
            # Refresh system prompt each turn so ingredient/preference edits apply
            if ss.messages and ss.messages[0]["role"] == "system":
                ss.messages[0]["content"] = system_prompt(ss.ingredients)
            else:
                ss.messages.insert(0, {"role": "system", "content": system_prompt(ss.ingredients)})
            ss.messages.append({"role": "user", "content": user_msg})
            with st.chat_message("assistant"):
                with st.status("Chef is thinking...", expanded=True) as status:
                    try:
                        answer = run_agent(ss.messages, status)
                        status.update(label="Done", state="complete")
                        quest_succeeded = True
                    except Exception as e:
                        answer = api_error_message(e)
                        status.update(label="Error", state="error")
                        quest_succeeded = False
                st.markdown(answer)
            ss.messages.append({"role": "assistant", "content": answer})
            ss.chat.append(("assistant", answer))
            if quest_succeeded:
                ss.xp += 50
                ss.quests_completed += 1
                st.balloons()
                st.toast("Dinner quest complete! +50 XP", icon="🍳")
                st.download_button("⬇️ Download this plan", answer, file_name="dinner_plan.md")
                st.caption("How did the chef do?")
                rating = st.feedback("stars")
                if rating is not None:
                    st.caption(f"Chef rating: {rating + 1}/5")
