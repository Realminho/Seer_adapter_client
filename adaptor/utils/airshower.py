# AirShower Workflow State Machine

# ---------------------------------------------------------------------------
# AirShower Scenario :
# A(ENTER)-->-->>Door>>----->---->B(INSIDE)----->----->>Door>>------C(PASSED)
# ---------------------------------------------------------------------------
# A's node action "ENTER"   INITIAL->PARING->WAITING_FOR_VACANCY->REQUESTING_OPEN(KEEP REQUESTING)->DONE (AMR moving to next)
# B's node action "INSIDE" "INITIAL->XXXXXX->XXXXXXXXXXXXXXXXXXX->REQUESTING_CLOSE-> WAITING_FOR_AIRFLOW->REQUESTING_OPEN(KEEP REQUESTING)->DONE ( AMR moving to next)
# C's node action "PASSED" "INITIAL->XXXXXX->XXXXXXXXXXXXXXXXXXX->REQUESTING_CLOSE-> UNPAIRING-> DONE ( AMR moving to next)

# ---------------------------
# Workflow Explanation
# ---------------------------
# INITIAL : Check PIO connection and IO module connection and connect
# PAIRING : Pairing the AS as slave ( TURN ON SELECT, send_channel, send_bc, TURN OFF SELECT ) until the response is connected
# WAITING_FOR_VACANCY : Waiting for the AS to be vacant ( Check the occupied pin until it is OFF ) 
# REQUESTING_OPEN : Requesting To open the door ( Turn ON action parameter value ( 0 or 1 ) until the requested value is ON )?UNPAIRING : UNPAIR the PIO master and client
# REQUESTING_CLOSE : Requesting To close door ( close both doors)
# WAITING_FOR_AIRFLOW : Airflow is working ( waiting for airflow fun turning on and then turingin off )
# UNPAIRING : UnPairing / stop connection between PIO master and client


# ---------------------------
# Example Usage 
# ---------------------------
# from utils.airshower import ASWorkflow
# async def main():
#     result, error_state, error_message = await ASWorkflow(door_open_pin=1, action="ENTER").workflow_sequence()
#     print(result, error_state, error_message)

#     result, error_state, error_message = await ASWorkflow(door_open_pin=1, action="INSIDE").workflow_sequence()
#     print(result, error_state, error_message)

#     result, error_state, error_message = await ASWorkflow(door_open_pin=0, action="PASSED").workflow_sequence()
#     print(result, error_state, error_message)


from enum import Enum, auto
import asyncio
from time import time
from config.config import get_config



class WorkflowAction(Enum):
    ENTER = "ENTER"
    INSIDE = "INSIDE"
    PASSED = "PASSED"


class WorkflowState(Enum):
    INITIAL = auto()
    PAIRING = auto()
    WAITING_FOR_VACANCY = auto()
    REQUESTING_OPEN = auto()
    REQUESTING_CLOSE = auto()
    WAITING_FOR_AIRFLOW = auto()
    UNPAIRING = auto()
    DONE = auto()
    FAILED = auto()


class AirShowerError(Enum):
    NONE = "NONE"
    TIMEOUT_AT_WAITING_FOR_VACANCY = "TIMEOUT at waiting for vacancy"
    TIMEOUT_AT_PAIRING = "TIMEOUT at PIO pairing master and client"
    TIMEOUT_AT_REQUESTING_OPEN = "TIMEOUT at door open requesting"
    TIMEOUT_AT_REQUESTING_CLOSE = "TIMEOUT at door close requesting"
    TIMEOUT_AT_WAITING_FOR_AIRFLOW = "TIMEOUT at waiting for airflow"
    ERROR_AT_GIVEN_ACTION = "Action should be either ENTER, INSIDE or PASSED"
    ERROR_AT_INITIAL = "ERROR at INITIAL"
    ERROR_AT_PAIRING = "ERROR at PAIRING"
    ERROR_AT_WAITING_FOR_VACANCY = "ERROR at WAITING_FOR_VACANCY"
    ERROR_AT_REQUESTING_OPEN = "ERROR at REQUESTING_OPEN"
    ERROR_AT_REQUESTING_CLOSE = "ERROR at REQUESTING_CLOSE"
    ERROR_AT_WAITING_FOR_AIRFLOW = "ERROR at WAITING_FOR_AIRFLOW"
    ERROR_AT_UNPAIRING = "ERROR at UNPAIRING"
    ERROR_AT_WORKFLOW_SEQUENCE = "ERROR at workflow sequence"


WORKFLOW_SEQUENCE = {
    WorkflowAction.ENTER: [
        WorkflowState.INITIAL,
        WorkflowState.PAIRING,
        WorkflowState.WAITING_FOR_VACANCY,
        WorkflowState.REQUESTING_OPEN,
        WorkflowState.DONE,
    ],

    WorkflowAction.INSIDE: [
        WorkflowState.INITIAL,
        WorkflowState.REQUESTING_CLOSE,
        WorkflowState.WAITING_FOR_AIRFLOW,
        WorkflowState.REQUESTING_OPEN,
        WorkflowState.DONE,
    ],

    WorkflowAction.PASSED: [
        WorkflowState.INITIAL,
        WorkflowState.REQUESTING_CLOSE,
        WorkflowState.UNPAIRING,
        WorkflowState.DONE,
    ],
}


class ASWorkflow:
    def __init__(
        self,
        door_open_pin: int,
        action: str,
        *,
        config_data=None,
        pio=None,
        ezi_io=None,
    ):
        # Get parameter from node' action parameter
        self.door_open_pin = door_open_pin

        try:
            self.action = WorkflowAction(str(action).strip().upper())
        except ValueError:
            self.action = None



        # work flow variable
        self.sequence = WORKFLOW_SEQUENCE.get(self.action, [])

        self.current_state = None
        self.state = WorkflowState.INITIAL
        self.running = True

        # PIO
        self.pio = pio
        
        # Ezi IO
        self.ezi_io = ezi_io

        # Get parameter from config file
        self.config_data = config_data if config_data is not None else get_config()

        # PIO related pin numbers  
        self.select_pin = self.config_data.ezi_config.select
        self.go_pin = self.config_data.ezi_config.go
        self.error_pin = self.config_data.air_shower_config.failure
        self.occupied_pin = self.config_data.air_shower_config.occupied
        self.fun_working_pin = self.config_data.air_shower_config.fun_working
        self.doors = self.config_data.air_shower_config.door_pin

        # PIO related config
        self.media = self.config_data.pio_config.media
        self.station_id = self.config_data.air_shower_config.pio_station_id
        self.channel = self.config_data.air_shower_config.channel
        self.port = self.config_data.pio_config.port
        self.vehicle_num = self.config_data.pio_config.vehicle_num

        # Timeout config
        self.timeout_pairing_requesting = self.config_data.air_shower_config.timeout_paring_requesting
        self.timeout_open_requesting = self.config_data.air_shower_config.timeout_open_requesting
        self.timeout_close_requesting = self.config_data.air_shower_config.timeout_close_requesting
        self.timeout_vacancy_waiting = self.config_data.air_shower_config.timeout_vacancy_waiting
        self.timeout_airflow_waiting = self.config_data.air_shower_config.timeout_airflow_waiting


        # Check before running the workflow
        self.occupied = False

        # if Workflow is started from INITIAL and paring OR SKIP INITIAL and paring, then the workflow is started from REQUESTING_OPEN state
        self.skip_initial_and_pairing = False

        # Can happen anytime
        self.error = False

        
        # Workflow result ( True : Success, False : Failed ) , If a state is failed, then the workflow is failed
        self.result = True
        self.error_message = ""
        self.error_state = AirShowerError.NONE

    async def run(self):
        if self.action is None:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_GIVEN_ACTION,
                "Action should be ENTER, INSIDE, or PASSED"
            )
            return False

        print(f"Start workflow: {self.action.value}")

        for state in self.sequence:
            self.current_state = state
            self.state = state

            print(f"Current state: {state.name}")

            ok = await self.handle_state(state)

            if not ok:
                self.current_state = WorkflowState.FAILED
                self.state = WorkflowState.FAILED
                self.result = False
                print("Workflow FAILED")
                return False

            if state == WorkflowState.DONE:
                self.result = True
                print("Workflow DONE")
                return True

        return True

    async def handle_state(self, state):
        if state == WorkflowState.INITIAL:
            return await self.handle_initial()

        if state == WorkflowState.PAIRING:
            return await self.handle_pairing()

        if state == WorkflowState.WAITING_FOR_VACANCY:
            return await self.handle_waiting_for_vacancy()

        if state == WorkflowState.REQUESTING_OPEN:
            return await self.handle_requesting_open()

        if state == WorkflowState.REQUESTING_CLOSE:
            return await self.handle_requesting_close()

        if state == WorkflowState.WAITING_FOR_AIRFLOW:
            return await self.handle_waiting_for_airflow()

        if state == WorkflowState.UNPAIRING:
            return await self.handle_unpairing()

        if state == WorkflowState.DONE:
            return await self.handle_done()

        return False

    #--------------------------------
    # HANDLER FUNCTIONS
    #--------------------------------

    async def handle_initial(self):
        # print("INITIAL")
        # return True
        try:

            # PIO related config
            _pa = getattr(self.config_data, "pio_advanced", None)
            if self.pio is None:
                from utils.pio import PIOMaster
                self.pio = PIOMaster(
                    self.config_data.pio_config.pio_serial_port,
                    self.config_data.pio_config.pio_baudrate,
                    timeout=_pa.socket_timeout_sec if _pa is not None else 0.2,
                    connect_delay_sec=_pa.connect_delay_sec if _pa is not None else 0.5,
                    read_frame_poll_sec=_pa.read_frame_poll_sec if _pa is not None else 0.05,
                    read_frames_wait_sec=_pa.read_frames_wait_sec if _pa is not None else 2.0,
                    send_wait_sec=_pa.send_wait_sec if _pa is not None else 2.0,
                )
            serial_port = getattr(self.pio, "ser", None)
            if serial_port is None or not bool(getattr(serial_port, "is_open", False)):
                self.pio.connect()

            # Ezi IO related config
            if self.ezi_io is None:
                from utils.ezi_io import EZIIOClient
                self.ezi_io = EZIIOClient(self.config_data.ezi_config.ezi_io)

            # Clear all outputs for PIO master
            await self.ezi_io.set_output(reset_mask=0xFFFF << 16)

            # Turn off SELECT pin for PIO master
            await self.ezi_io.turn_off_output(self.select_pin)
            # await self.ezi_io.set_output(reset_mask=1 << 31)

            return True


        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_INITIAL,
                f"Error in handle_initial: {e}"
            )
            return False

    async def handle_pairing(self):
        # print("PAIRING")
        # return True
        try:                      
            # Paring the PIO master and client
            await self.pairing()
            await asyncio.sleep(0.2)
            
            # TIMEOUT CHECK: Check if the GO pin is ON until the timeout for checking paring is successed or not
            # Request Paring until the GO pin is ON
            start_time = time()
            while not (await self.ezi_io.get_input_pin(self.go_pin)):

                await self.pairing()
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                if time() - start_time > self.timeout_pairing_requesting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, AirShowerError.TIMEOUT_AT_PARING, f"Timeout at PIO paring master and client")
                    return False
                # print("PAIRING: Waiting for GO pin to be ON")
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))
            
            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_PAIRING,
                f"Error in handle_pairing: {e}"
            )
            return False

    async def handle_waiting_for_vacancy(self):

        # print("WAITING_FOR_VACANCY")
        # return True
        try:
            out = await self.ezi_io.get_input_pin(self.occupied_pin)
            await asyncio.sleep(0.2)
            # wait until Occupied pin is OFF
            start_time = time()
            # while out != 1:
            while out:
                out = await self.ezi_io.get_input_pin(self.occupied_pin)
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                if time() - start_time > self.timeout_vacancy_waiting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, AirShowerError.TIMEOUT_AT_WAITING_FOR_VACANCY, f"Timeout at the vacancy waiting")
                    return False
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_WAITING_FOR_VACANCY,
                f"Error in handle_waiting_for_vacancy: {e}"
            )
            return False

    async def handle_requesting_open(self):
        # print("REQUESTING_OPEN")
        # return True
        try:
            
            await self.ezi_io.turn_on_output(self.door_open_pin)
            await asyncio.sleep(0.2)

            out = await self.ezi_io.get_output_pin(self.door_open_pin)
            await asyncio.sleep(0.2)


            # wait until the door is opened
            start_time = time()
            # while out != 1:
            while not out:

                await self.ezi_io.turn_on_output(self.door_open_pin)
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                out = await self.ezi_io.get_output_pin(self.door_open_pin)
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                if time() - start_time > self.timeout_open_requesting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, AirShowerError.TIMEOUT_AT_REQUESTING_OPEN, f"Timeout at door open requesting")
                    return False
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

            return True


        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_REQUESTING_OPEN,
                f"Error in handle_requesting_open: {e}"
            )
            return False

    async def handle_requesting_close(self):
        # print("REQUESTING_CLOSE")
        # return True

        try:

           
            await self.ezi_io.turn_off_output(self.doors[0])
            await asyncio.sleep(0.2)

            await self.ezi_io.turn_off_output(self.doors[1])
            await asyncio.sleep(0.2)

            door_0 = await self.ezi_io.get_output_pin(self.doors[0])
            await asyncio.sleep(0.2)

            door_1 = await self.ezi_io.get_output_pin(self.doors[1])
            await asyncio.sleep(0.2)


            # wait until the door is opened
            start_time = time()
            # while out != 1:
            while not (door_0 == 0 and door_1 == 0):

                await self.ezi_io.turn_off_output(self.doors[0])
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                await self.ezi_io.turn_off_output(self.doors[1])
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                door_0 = await self.ezi_io.get_output_pin(self.doors[0])
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                door_1 = await self.ezi_io.get_output_pin(self.doors[1])
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                if time() - start_time > self.timeout_close_requesting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, AirShowerError.TIMEOUT_AT_REQUESTING_CLOSE, f"Timeout at door close requesting")
                    return False
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_REQUESTING_CLOSE,
                f"Error in handle_requesting_close: {e}"
            )
            return False

    
    
    async def handle_waiting_for_airflow(self):
        # print("WAITING_FOR_AIRFLOW")
        # return True
        try:
            
            out = await self.ezi_io.get_input_pin(self.fun_working_pin)
            await asyncio.sleep(0.2)


            # wait until fun working pin is ON and OFF ( Air FLow starts and stops)
            start_time = time()
            # while out != 1:
            while not out: # wait for air flow starts
                out = await self.ezi_io.get_input_pin(self.fun_working_pin)
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                if time() - start_time > self.timeout_airflow_waiting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, AirShowerError.TIMEOUT_AT_WAITING_FOR_AIRFLOW, f"Timeout at air shower fun working")
                    return False
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))


            out = await self.ezi_io.get_input_pin(self.fun_working_pin)
            await asyncio.sleep(0.2)

            while out:   # wait for air flow ends
                out = await self.ezi_io.get_input_pin(self.fun_working_pin)
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

                if time() - start_time > self.timeout_open_requesting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, AirShowerError.TIMEOUT_AT_WAITING_FOR_AIRFLOW, f"Timeout at air shower fun working")
                    return False
                await asyncio.sleep(getattr(self.config_data.air_shower_config, "poll_interval_sec", 0.2))

            # after air shower fun ON and OFF successfully
            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_WAITING_FOR_AIRFLOW,
                f"Error in handle_waiting_for_airflow: {e}"
            )
            return False

    async def handle_unpairing(self):
        # print("UNPAIRING")
        # return True

        try:
            
            await self.ezi_io.turn_on_output(self.select_pin)
            await asyncio.sleep(0.2)

            await self.ezi_io.turn_off_output(self.select_pin)
            await asyncio.sleep(0.2)

            # Clear all outputs for PIO master
            await self.ezi_io.set_output(reset_mask=0xFFFF << 16)
            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_UNPAIRING,
                f"Error in handle_unpairing: {e}"
            )
            return False

    async def handle_done(self):
        # print("DONE: Workflow finished")
        # return True
        self.workflow_result(
            WorkflowState.DONE,
            True,
            AirShowerError.NONE,
            ""
        )
        return True

    #--------------------------------
    # Helper functions
    #--------------------------------

    async def is_already_paired(self):
        if self.ezi_io is None:
            return self.state

        if await self.ezi_io.get_input_pin(self.go_pin):
            self.skip_initial_and_pairing = True
            return WorkflowState.REQUESTING_OPEN

        return self.state


    def workflow_result(
        self,
        next_state: WorkflowState,
        result: bool,
        error_state: AirShowerError,
        error_message: str
    ):
        self.state = next_state
        self.result = result
        self.error_state = error_state
        self.error_message = error_message

    async def pairing(self):
        try:
            await self.ezi_io.turn_on_output(self.select_pin)
            await asyncio.sleep(0.2)

            self.pio.send_bc(
                media=self.media,
                station_id=self.station_id,
                channel=self.channel,
                port=self.port,
                oht_num=self.vehicle_num
            )
            await asyncio.sleep(0.2)

            await self.ezi_io.turn_off_output(self.select_pin)
            await asyncio.sleep(0.2)

            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                AirShowerError.ERROR_AT_PAIRING,
                f"Error in pairing: {e}"
            )
            return False

    #--------------------------------
    # Main Workflow Sequence
    #--------------------------------

    async def workflow_sequence(self):
        try:
            await self.run()
            return self.result, self.error_state, self.error_message

        except Exception as e:
            return (
                False,
                AirShowerError.ERROR_AT_WORKFLOW_SEQUENCE,
                f"Error in workflow_sequence: {e}"
            )






