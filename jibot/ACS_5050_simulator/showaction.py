import json
import os
from paho.mqtt import client as mqtt

BROKER = "192.168.3.112"
PORT = 1883
TOPIC = "amr/v3/HN-SH6-TR-002/state"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_actions(title, actions):
    print(f"\n=== {title} ===")

    if not actions:
        print("None")
        return

    print(
        f"{'Action ID':<15} "
        f"{'Status':<10} "
        f"{'Type':<25} "
        f"{'Description'}"
    )
    print("-" * 80)

    for action in actions:
        print(
            f"{action.get('actionId', ''):<15} "
            f"{action.get('actionStatus', ''):<10} "
            f"{action.get('actionType', ''):<25} "
            f"{action.get('actionDescription', '')}"
        )


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected")
        client.subscribe(TOPIC)
        print(f"Subscribed: {TOPIC}")
    else:
        print(f"Connection failed: {rc}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())

        clear()

        # print("=" * 100)
        # print("AGV ACTION STATE MONITOR")
        # print("=" * 100)

        # print(f"Order ID              : {payload.get('orderId')}")
        # print(f"Order Update ID       : {payload.get('orderUpdateId')}")
        # print(f"Last Node ID          : {payload.get('lastNodeId')}")
        # print(f"Last Node Sequence ID : {payload.get('lastNodeSequenceId')}")
        # print(f"Driving               : {payload.get('driving')}")
        # print(f"Operating Mode        : {payload.get('operatingMode')}")

        print_actions("ACTION STATES", payload.get("actionStates", []))
        print_actions("INSTANT ACTION STATES", payload.get("instantActionStates", []))

    except Exception as e:
        print(f"Error parsing message: {e}")


client = mqtt.Client()

client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)

client.loop_forever()
