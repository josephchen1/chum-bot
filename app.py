import re, os
from utils import *

from flask import Flask, request

from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_bolt.oauth.oauth_settings import OAuthSettings

from apscheduler.schedulers.background import BackgroundScheduler

from dotenv import load_dotenv
from flask_pymongo import PyMongo

CHUM_WORDS = ["chum", "chummed", "chumming", "chums"]
OAUTH_EXPIRATION_SECONDS = 600
EDIT_GRACE_PERIOD_SECONDS = 60
REFERENDUM_WINDOW_SECONDS = 86400 # change to 86400
REFERENDUM_EXPIRATION_SECONDS = 86400 # change to 86400
REFERENDUM_CHECK_SECONDS = 600 # Change to 600
BASE = "/spotbot"

CHUM_PATTERN = comp(r"(\b" + r"\b)|(\b".join(CHUM_WORDS) + r"\b)")
USER_PATTERN = re.compile(r"<@[a-zA-Z0-9]+>")

APPROVED_EMOJI = "white_check_mark"
DENIED_EMOJI = "x"

load_dotenv()

app = Flask("app")
app.config["MONGO_URI"] = os.environ.get("SPOTBOT_SECURE_LINK")
mongo = PyMongo(app)
db_client = mongo.cx
chum_data = SpotDatabase(db_client)
referendum_data = ReferendumDatabase(db_client, REFERENDUM_EXPIRATION_SECONDS)

# https://slack.dev/bolt-python/concepts#authenticating-oauth
oauth_settings = OAuthSettings(
    client_id=os.environ.get("SPOTBOT_CLIENT_ID"),
    client_secret=os.environ.get("SPOTBOT_CLIENT_SECRET"),
    scopes=[
        "channels:history",
        "chat:write",
        "files:read",
        "groups:history",
        "im:history",
        "mpim:read",
        "reactions:write",
        "users.profile:read",
        "reactions:read",
        "channels:read",
        "groups:read"
    ],
    installation_store=DatabaseInstallationStore(db_client),
    state_store=DatabaseOAuthStateStore(db_client, expiration_seconds=OAUTH_EXPIRATION_SECONDS),
    install_path=f"{BASE}/install/",
    redirect_uri_path=f"{BASE}/oauth_redirect/"
)

bolt_app = App(
    signing_secret=os.environ.get("SPOTBOT_SIGNING_SECRET"), 
    oauth_settings=oauth_settings
)

handler = SlackRequestHandler(bolt_app)

@app.route(f"{BASE}/install/")
def handle_install():
    return handler.handle(request)

@app.route(f"{BASE}/oauth_redirect/", methods=["GET"])
def handle_oauth():
    return handler.handle(request)

@app.route(f"{BASE}/events/", methods=["POST"])
def handle_events():
    return handler.handle(request)

@bolt_app.event("member_joined_channel")
def joined_listener(event, body, say, client):
    if event["user"] != get_bot_user(client):
        return 
    with open("chumbot_intro.txt") as file:
        say(file.read())

    if "inviter" not in event:
        return 

    chum_data.configure_for_message(event, body)
    chum_data.set_manager(event["inviter"])
    chum_data.push_write()

@bolt_app.message(CHUM_PATTERN)
def spot_listener(event, body, say, client):
    if "files" not in event:
        return
    chum_data.configure_for_message(event, body)
    log_spot(event["channel"], event["user"], event["ts"], event["text"], event["files"], say, client)
    chum_data.push_write()

# Assumes matches CHUM_PATTERN and files are present. 
def log_spot(channel, user, ts, text, files, say, client, purged_recent=False):
    spotter = user
    found_spotted = USER_PATTERN.findall(text)
    found_spotted = list(set(found_spotted)) # remove duplicates
    found_spotted = [username[2:-1] for username in found_spotted]
    if spotter in found_spotted:
        found_spotted.remove(spotter)

    bot_user = get_bot_user(client)
    if bot_user in found_spotted:
        found_spotted.remove(bot_user)
    
    if not found_spotted:
        return 

    all_images = [image['url_private'] for image in files]

    chum_data.increment_spot(spotter, len(found_spotted))
    for spotted in found_spotted:
        chum_data.increment_caught(spotted, 1)
        chum_data.append_images(spotted, all_images)

    chum_data.add_message(message_id(ts), {
        "spotter": spotter,
        "spotted": found_spotted,
        "images": all_images,
        "ts": ts,
        "referendum": False
    })

    if not purged_recent: 
        recent = chum_data.get_recent()
        if recent == spotter: 
            say(f"<@{spotter}> is on fire 🥵")
            chum_data.set(RECENT, None)
        else: 
            chum_data.set(RECENT, spotter)
    else: 
        chum_data.set(RECENT, spotter)

    client.reactions_add(channel=channel, name=APPROVED_EMOJI, timestamp=ts)

@bolt_app.event({
    "type": "message",
    "subtype": "message_deleted"
})
def delete_listener(event, body):
    chum_data.configure_for_message(event, body)
    delete(message_id(event["deleted_ts"]))
    chum_data.push_write()

def delete(mid):
    message = chum_data.delete_message(mid)
    if not message:
        return
    chum_data.increment_spot(message["spotter"], -1 * len(message["spotted"]))
    for user in message["spotted"]:
        chum_data.increment_caught(user, -1)
        chum_data.update_value(f"{IMAGES}.{user}", "$pull", message["images"])

    chum_data.set(RECENT, None)

@bolt_app.event({
    "type": "message",
    "subtype": "message_changed"
})
def changed_listener(event, body, say, client):
    chum_data.configure_for_message(event, body)
    inner_event = event["message"]

    if "files" not in inner_event:
        return

    if not CHUM_PATTERN.search(inner_event["text"]):
        return

    if float(event["ts"]) - float(inner_event["ts"]) > EDIT_GRACE_PERIOD_SECONDS:
        # Only accept edits made soon after they are posted
        return 

    # If spots have been counted, they must be deleted and recounted.
    try:
        delete(message_id(inner_event["ts"]))
        client.reactions_remove(channel=event["channel"], name=APPROVED_EMOJI, timestamp=inner_event["ts"])
    except Exception as e:
        print("Encountered an exception while internally deleting a changed spot: ", e)

    log_spot(event["channel"], inner_event["user"], inner_event["ts"], 
        inner_event["text"], inner_event["files"], say, client, purged_recent=True)

    chum_data.push_write()

@bolt_app.message(comp("scoreboard|chumboard"))
def scoreboard_listener(event, say, body, client):
    try:
        words = event['text'].lower().split()
        n = int(words[words.index("chumboard") + 1])
    except:
        try:
            n = int(words[words.index("scoreboard") + 1])
        except:
            n = 5

    chum_data.configure_for_message(event, body)
    spots = chum_data.get({SPOT: True})
    if not spots:
        return 
    spots = spots[SPOT]
    scoreboard = sorted(spots.keys(), key=lambda p: spots[p], reverse=True)[:n]
    message = "chumboard:\n" 
    for i, participant in enumerate(scoreboard):
        message += f"{i + 1}. {get_display_name(client, participant)} - {spots[participant]}\n" 
    say(message)

@bolt_app.message(comp(r"\bpics\b|\bphotos\b"))
def pics_listener(event, say, body, client):
    found_spotted = USER_PATTERN.search(event['text'])
    if not found_spotted:
        return
    spotted = found_spotted[0][2:-1]

    chum_data.configure_for_message(event, body)
    images = chum_data.get({IMAGES: True})
    if not images:
        return 
    images = images[IMAGES]

    message = f"Spots of {get_display_name(client, spotted)}:\n"
    for i, link in enumerate(images[spotted]):
        message += f"{i + 1}. {link}\n"
    say(message)

@bolt_app.message(comp(r"\breferendum\b"))
def referendum_listener(event, say, body, client):
    if "thread_ts" not in event:
        return

    if float(event["ts"]) - float(event["thread_ts"]) > REFERENDUM_WINDOW_SECONDS:
        # Only accept referendums that open within a certain window
        return 

    chum_data.configure_for_message(event, body)
    mid = message_id(event["thread_ts"])
    result = chum_data.set_referendum(mid, True)
    if result is not False:
        return 

    referendum_post = say(
        text = f"Good chum :+1: or bad chum :-1:? ", 
        thread_ts = event['thread_ts'],
        reply_broadcast = True
    )
    
    referendum_data.store_referendum({
        "spot_ts": event['thread_ts'], 
        "vote_ts": referendum_post["ts"],
        "channel_id": event["channel"],
        "team_id": body["team_id"],
        "loc_id": unique_location_identifier(event, body),
        "date": datetime.utcnow()
    })

    client.reactions_add(channel=referendum_post["channel"], name="+1", timestamp=referendum_post["ts"])
    client.reactions_add(channel=referendum_post["channel"], name="-1", timestamp=referendum_post["ts"])

@bolt_app.message(comp(r"\breset\b"))
def reset_listener(event, say, body, client):
    chum_data.configure_for_message(event, body)
    manager = chum_data.get_manager()
    if event["user"] != manager: 
        say("Only the person who invited Chum Bot to the channel can perform that action. ")
        return 

    if not re.search("reset yes i mean it really delete everything", event["text"], re.IGNORECASE):
        say("If you really want to delete every chum in this channel, please send \"reset yes i mean it really delete everything\". This action cannot be undone.")
        return
    
    say("Resetting the chum record. ")
    chum_data.drop_loc(manager)

def process_referenda():
    for referendum in referendum_data.expired_referenda(): 
        try: 
            process_referendum(referendum)
        except Exception as e:
            print("Encountered an exception while processing expired referenda: ", e)

def process_referendum(referendum):
    bot = bolt_app.installation_store.find_installation(team_id=referendum["team_id"], enterprise_id=None, user_id=None, is_enterprise_install=None)
    result = bolt_app.client.reactions_get(token=bot.bot_token, channel=referendum["channel_id"], timestamp=referendum["vote_ts"])
    yes_votes = set()
    no_votes = set()
    for reaction in result["message"]["reactions"]:
        names = reaction["name"].split("::")
        if names[0] == "+1" or names[0] == "thumbsup":
            ledger = yes_votes
        elif names[0] == "-1" or names[0] == "thumbsdown":
            ledger = no_votes
        else: 
            continue
        for user in reaction["users"]:
            ledger.add(user)

    if len(yes_votes) >= len(no_votes): 
        bolt_app.client.chat_postMessage(token=bot.bot_token, channel=referendum["channel_id"], thread_ts=referendum["spot_ts"], text="The chum is good! ")
        return 

    chum_data.configure_for_loc(referendum["loc_id"])
    delete(message_id(referendum["spot_ts"]))
    chum_data.push_write()
    bolt_app.client.reactions_remove(token=bot.bot_token, channel=referendum["channel_id"], name=APPROVED_EMOJI, timestamp=referendum["spot_ts"])
    bolt_app.client.reactions_add(token=bot.bot_token, channel=referendum["channel_id"], name=DENIED_EMOJI, timestamp=referendum["spot_ts"])
    bolt_app.client.chat_postMessage(token=bot.bot_token, channel=referendum["channel_id"], thread_ts=referendum["spot_ts"], text="The chum is bad. ")

scheduler = BackgroundScheduler()
scheduler.add_job(func=process_referenda, trigger="interval", seconds=REFERENDUM_CHECK_SECONDS)
scheduler.start()
process_referenda()

@bolt_app.event("file_shared")
@bolt_app.event("message")
def ignore(event):
    pass
