import json
import os
from paho.mqtt import client as mqtt

BROKER = "192.168.3.112"
PORT = 1883
TOPIC = "amr/v3/HN-SH6-TR-002/state"



def clear():
    os.system("cls" if os.name == "nt" else "clear")


def print_actions(actions):
    print("\n=== ACTION STATES ===")

    print(
        f"{'Action ID':<15} "
        f"{'Status':<10} "
        f"{'Type':<25} "
        f"{'Description'}"
    )

    print("-" * 80)

    for action in actions:
        print(
            f"{action.get('actionId',''):<15} "
            f"{action.get('actionStatus',''):<10} "
            f"{action.get('actionType',''):<25} "
            f"{action.get('actionDescription','')}"
        )


def print_errors(errors):
    print("\n=== ERRORS ===")

    if not errors:
        print("No active errors")
        return

    for error in errors:
        print(f"\nType        : {error.get('errorType')}")
        print(f"Level       : {error.get('errorLevel')}")
        print(f"Description : {error.get('errorDescription')}")

        refs = error.get("errorReferences", [])

        for ref in refs:
            print(
                f"  {ref.get('referenceKey')} : "
                f"{ref.get('referenceValue')}"
            )


def print_information(information):
    print("\n=== INFORMATION ===")

    if not information:
        print("No information")
        return

    for info in information[-5:]:
        print(f"\nType        : {info.get('infoType')}")
        print(f"Level       : {info.get('infoLevel')}")
        print(f"Description : {info.get('infoDescription')}")

        refs = info.get("infoReferences", [])

        for ref in refs:
            print(
                f"  {ref.get('referenceKey')} : "
                f"{ref.get('referenceValue')}"
            )


def print_battery(payload):
    battery = payload.get("batteryState", {})

    print("\n=== BATTERY ===")

    print(
        f"Charge   : {battery.get('batteryCharge', 0):.1f}%"
    )

    print(
        f"Voltage  : {battery.get('batteryVoltage', 0)}V"
    )

    print(
        f"Charging : {battery.get('charging', False)}"
    )


def print_position(payload):
    pos = payload.get("agvPosition", {})

    print("\n=== AGV POSITION ===")

    print(f"Map   : {pos.get('mapId')}")
    print(f"X     : {pos.get('x')}")
    print(f"Y     : {pos.get('y')}")
    print(f"Theta : {pos.get('theta')}")


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

        print("=" * 100)
        print("AGV STATE MONITOR")
        print("=" * 100)

        print(
            f"Order ID      : {payload.get('orderId')}"
        )

        print(
            f"Order Update ID      : {payload.get('orderUpdateId')}"
        )

        print(
            f"Last Node ID    : {payload.get('lastNodeId')}"
        )

        print(
            f"Last Node Sequence ID    : {payload.get('lastNodeSequenceId')}"
        )

        print(
            f"Driving       : {payload.get('driving')}"
        )

        print(
            f"OperatingMode : {payload.get('operatingMode')}"
        )

        # print_actions(payload.get("actionStates", []))
        print_errors(payload.get("errors", []))
        print_information(payload.get("information", []))
        # print_battery(payload)
        # print_position(payload)

    except Exception as e:
        print(f"Error parsing message: {e}")


client = mqtt.Client()

client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT, 60)

client.loop_forever()