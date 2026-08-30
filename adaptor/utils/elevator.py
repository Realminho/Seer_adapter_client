# Elevator Workflow State Machine  
# First Floor Wall has attached PIO client
# Second Floor Wall has attached PIO client
# AMR has PIO master

# ---------------------------------------------------------------------------
# Elevator Scenario :
# A(ENTER)-->-->>Door>>----->---->B(INSIDE)----->----->>Door>>------C(PASSED)
# ---------------------------------------------------------------------------
# A's node action "ENTER"   INITIAL-> PARING -> REQUESTING_FLOOR -> REQUESTING_OPEN -> DONE
# B's node action "INSIDE" "INITIAL-> REQUESTING_CLOSE -> GOTO_FLOOR -> UNPAIRING-> WAITING_FOR_ELEVATING -> PARING -> DONE
# C's node action "PASSED" "INITIAL-> REQUESTING_CLOSE -> UNPAIRING-> DONE ( Map changed and localized )

# ---------------------------
# Workflow Explanation
# ---------------------------
# INITIAL : Check PIO connection and IO module connection and connect
# PAIRING : Pairing the AS as slave ( TURN ON SELECT, send_channel, send_bc, TURN OFF SELECT ) until the response is connected
# REQUESTING_FLOOR : Asking Elevator to come to a speicific floor ( Floor pin blinking into SOLID ) ( AMR is outside of Elevator )
# GOTO_FLOOR : Asking elevator to go to a specifi floor   ( AMR is inside of Elevator and go togther with elevator )
# REQUESTING_OPEN: Requesting to open the door ( turn ON door open PIO number and turn OFF that number )
# REQUESTING_CLOSE : Requesting to close the door ( turn ON door close PIO number and turn OFF that number )
# WAITING_FOR_ELEVATING : Waiting for a specific time for elevating between floors
# UNPAIRING : UnPairing / stop connection between PIO master and client


# ---------------------------
# Example Usage 
# ---------------------------
# from utils.elevator import EVWorkflow
# async def main():

#     ask elevator to come the floor 1
#     result, error_state, error_message = await EVWorkflow(floor_pin=rule.floor_pin,pio_station_id=rule.pio_station_id, channel=config.elevator_config.channel, action=rule.mode).workflow_sequence()
#     print(result, error_state, error_message)




from enum import Enum, auto
import asyncio
from time import time
from config.config import get_config

class WorkflowAction(Enum):
    ENTER = "ENTER"
    INSIDE = "INSIDE"
    PASSED = "PASSED"
    TEST_PAIR = "TEST_PAIR"
    TEST_UNPAIR = "TEST_UNPAIR"
    TEST_OPEN = "TEST_OPEN"
    TEST_CLOSE = "TEST_CLOSE"
    TEST_GOTO_FLOOR = "TEST_GOTO_FLOOR"


class WorkflowState(Enum):
    INITIAL = auto()
    PAIRING = auto()
    REQUESTING_FLOOR = auto()
    GOTO_FLOOR = auto()
    REQUESTING_OPEN = auto()
    REQUESTING_CLOSE = auto()
    WAITING_FOR_ELEVATING = auto()
    UNPAIRING = auto()
    DONE = auto()
    FAILED = auto()

class ElevatorError(Enum):
    NONE = "NONE"
    TIMEOUT_AT_PAIRING = "TIMEOUT at PIO pairing master and client"
    TIMEOUT_AT_REQUESTING_FLOOR = "TIMEOUT at requesting floor"
    ERROR_AT_GIVEN_ACTION = "Action should be either ENTER, INSIDE or PASSED"
    ERROR_AT_INITIAL = "ERROR at INITIAL"
    ERROR_AT_PAIRING = "ERROR at PAIRING"
    ERROR_AT_REQUESTING_FLOOR = "ERROR at requesting floor"
    ERROR_AT_REQUESTING_OPEN = "ERROR at REQUESTING_OPEN"
    ERROR_AT_REQUESTING_CLOSE = "ERROR at REQUESTING_CLOSE"
    ERROR_AT_GOTO_FLOOR = "ERROR at goto floor"
    ERROR_AT_UNPAIRING = "ERROR at UNPAIRING"
    ERROR_AT_WORKFLOW_SEQUENCE = "ERROR at workflow sequence"


WORKFLOW_SEQUENCE = {
    WorkflowAction.TEST_OPEN: [
        WorkflowState.INITIAL,
        WorkflowState.PAIRING,
        WorkflowState.REQUESTING_OPEN,
        WorkflowState.UNPAIRING,
        WorkflowState.DONE,
    ],
    WorkflowAction.TEST_CLOSE: [
        WorkflowState.INITIAL,
        WorkflowState.PAIRING,
        WorkflowState.REQUESTING_CLOSE,
        WorkflowState.UNPAIRING,
        WorkflowState.DONE,
    ],
    WorkflowAction.TEST_GOTO_FLOOR: [
        WorkflowState.INITIAL,
        WorkflowState.PAIRING,
        WorkflowState.REQUESTING_FLOOR,
        WorkflowState.UNPAIRING,
        WorkflowState.DONE,
    ],
    WorkflowAction.TEST_PAIR: [
        WorkflowState.INITIAL,
        WorkflowState.PAIRING,
        WorkflowState.DONE,
    ],
    WorkflowAction.TEST_UNPAIR: [
        WorkflowState.INITIAL,
        WorkflowState.UNPAIRING,
        WorkflowState.DONE,
    ],
    WorkflowAction.ENTER: [
        WorkflowState.INITIAL,
        WorkflowState.PAIRING,
        WorkflowState.REQUESTING_FLOOR,
        WorkflowState.DONE,
    ],

    WorkflowAction.INSIDE: [
        WorkflowState.INITIAL,
        WorkflowState.REQUESTING_CLOSE,
        WorkflowState.GOTO_FLOOR,
        WorkflowState.UNPAIRING,
        WorkflowState.WAITING_FOR_ELEVATING,
        WorkflowState.PAIRING,
        WorkflowState.DONE,
    ],

    WorkflowAction.PASSED: [
        WorkflowState.INITIAL,
        WorkflowState.REQUESTING_CLOSE,
        WorkflowState.UNPAIRING,
        WorkflowState.DONE,
    ],
}


class EVWorkflow:
    def __init__(
        self,
        floor_pin: int,
        pio_station_id: str,
        action: str,
        *,
        channel: int,
        config_data=None,
        pio=None,
        ezi_io=None,
    ):

        self.floor_pin = floor_pin

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
        
        # Elevator related pin numbers
        self.open_door_pin =self.config_data.elevator_config.open_door_pin
        self.close_door_pin =self.config_data.elevator_config.close_door_pin
        self.solid_on_second = self.config_data.elevator_config.solid_on_second
        self.elevating_timing_second = self.config_data.elevator_config.elevating_timing_second
        self.door_open_close_timing_second = self.config_data.elevator_config.door_open_close_timing_second
       
        # PIO related config
        self.media = self.config_data.pio_config.media
        self.station_id = pio_station_id
        self.channel = channel
        self.port = self.config_data.pio_config.port
        self.vehicle_num = self.config_data.pio_config.vehicle_num

        # Timeout config
        self.timeout_paring_requesting = self.config_data.elevator_config.timeout_paring_requesting
        self.timeout_floor_requesting = self.config_data.elevator_config.timeout_floor_requesting

        # Check before running the workflow
        self.occupied = False

        # if Workflow is started from INITIAL and paring OR SKIP INITIAL and paring, then the workflow is started from REQUESTING_OPEN state
        self.skip_initial_and_pairing = False

        # Can happen anytime
        self.error = False

        
        # Workflow result ( True : Success, False : Failed ) , If a state is failed, then the workflow is failed
        self.result = True
        self.error_message = ""
        self.error_state = ElevatorError.NONE

    async def run(self):
        if self.action is None:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_GIVEN_ACTION,
                "Action should be ENTER, INSIDE, or PASSED"
            )
            return False

        # print(f"Start workflow: {self.action.value}")

        for state in self.sequence:
            self.current_state = state
            self.state = state

            print(f"Current state: {state.name}")

            ok = await self.handle_state(state)

            if not ok:
                self.current_state = WorkflowState.FAILED
                self.state = WorkflowState.FAILED
                self.result = False
                # print("Workflow FAILED")
                return False

            if state == WorkflowState.DONE:
                self.result = True
                # print("Workflow DONE")
                return True

        return True

    async def handle_state(self, state):
        if state == WorkflowState.INITIAL:
            return await self.handle_initial()

        if state == WorkflowState.PAIRING:
            return await self.handle_pairing()

        if state == WorkflowState.REQUESTING_FLOOR:
            return await self.handle_requesting_floor()

        if state == WorkflowState.GOTO_FLOOR:
            return await self.handle_goto_floor()

        if state == WorkflowState.REQUESTING_OPEN:
            return await self.handle_requesting_open()

        if state == WorkflowState.REQUESTING_CLOSE:
            return await self.handle_requesting_close()

        if state == WorkflowState.WAITING_FOR_ELEVATING:
            return await self.handle_waiting_for_elevating()

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
            if self.pio is None:
                from utils.pio import PIOMaster
                self.pio = PIOMaster(
                    self.config_data.pio_config.pio_serial_port,
                    self.config_data.pio_config.pio_baudrate,
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


            # print (await self.ezi_io.get_input_pin(self.go_pin))
            await asyncio.sleep(0.2)

            return True


        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_INITIAL,
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
                await asyncio.sleep(0.2)
                
                if time() - start_time > self.timeout_paring_requesting * 60:
                    self.workflow_result( WorkflowState.FAILED, False, ElevatorError.TIMEOUT_AT_PARING, f"Timeout at PIO paring master and client")
                    return False
                # print("PAIRING: Waiting for GO pin to be ON")
                await asyncio.sleep(0.2)
            
            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_PAIRING,
                f"Error in handle_pairing: {e}")
            return False

    async def handle_requesting_floor(self):

        # Check 
        # 1) requested floor is already PIN Solid ON ( door is closed / available )
        # 2) requested floor is already PIN Solid ON ( door is opened / occupied )
        # 3) other floor ( door is closed / available )
        # 4) other floor ( door is opened / occupied )

        try:

            # if requested floor is Solid ON ( Close the door and Open the door )
            if await self.ezi_io.get_input_pin(self.floor_pin):
            # if await self.ezi_io.get_output_pin(0):
                # print("already there")

                await self.ezi_io.turn_on_output(self.close_door_pin)
                await asyncio.sleep(0.2)

                await self.ezi_io.turn_off_output(self.close_door_pin)
                await asyncio.sleep(0.2)

                await self.ezi_io.turn_on_output(self.open_door_pin)
                await asyncio.sleep(0.2)

                await self.ezi_io.turn_off_output(self.open_door_pin)
                await asyncio.sleep(0.2)

                return True
            
            else: # if requested floor is OFF ( call elevator to the requested floor )

                await self.ezi_io.turn_on_output(self.floor_pin)
                await asyncio.sleep(0.2)
                # print("other floor")


                start_time = time()

                while not (await self.ezi_io.get_output_pin(self.floor_pin)):
                    
                    await self.ezi_io.turn_on_output(self.floor_pin)
                    await asyncio.sleep(0.2)
                    
                    if time() - start_time > self.timeout_floor_requesting * 60:

                        # if timeout , unpair
                        await self.ezi_io.turn_on_output(self.select_pin)
                        await asyncio.sleep(0.2)

                        await self.ezi_io.turn_off_output(self.select_pin)
                        await asyncio.sleep(0.2)

                        # if timeout ,Clear all outputs for PIO master
                        await self.ezi_io.set_output(reset_mask=0xFFFF << 16)

                        self.workflow_result( WorkflowState.FAILED, False, ElevatorError.TIMEOUT_AT_REQUESTING_FLOOR, f"Timeout at the requesting floor")
                        return False

                await asyncio.sleep(self.elevating_timing_second)

                # 호출한 층에 이미 있던 위 분기와 마찬가지로 성공을 알린다.
                # 여기서 떨어지면 run()이 None을 실패로 읽어, 엘리베이터가
                # 다른 층에 있을 때 ENTER가 항상 실패한다.
                return True
                return True



                # start_time = time()

                # while True:
                #     # print(await self.ezi_io.get_input_pin(self.floor))

                #     remaining_timeout = self.timeout_floor_requesting * 60 - (time() - start_time)
                #     if remaining_timeout <= 0:

                #         # if timeout , unpair
                #         await self.ezi_io.turn_on_output(self.select_pin)
                #         await asyncio.sleep(0.2)

                #         await self.ezi_io.turn_off_output(self.select_pin)
                #         await asyncio.sleep(0.2)

                #         # if timeout ,Clear all outputs for PIO master
                #         await self.ezi_io.set_output(reset_mask=0xFFFF << 16)


                #         self.workflow_result(
                #             WorkflowState.FAILED,
                #             False,
                #             ElevatorError.TIMEOUT_AT_REQUESTING_FLOOR,
                #             "Timeout at the requesting floor"
                #         )
                #         return False

                #     solid = await self.is_solid_on(
                #         self.floor,
                #         required_duration=self.solid_on_second,
                #         poll_interval=0.1,
                #         timeout=remaining_timeout
                #     )

                #     if solid:
                #         return True
                #     # else: loop back and keep pressing open_door / retrying

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_REQUESTING_FLOOR,
                f"Error in handle_requesting_floor: {e}"
            )
            return False

    async def handle_requesting_open(self):
        # print("REQUESTING_OPEN_DOOR")
        # return True
        try:
            # print(self.open_door_pin)
            await self.ezi_io.turn_on_output(self.open_door_pin)
            await asyncio.sleep(0.2)

            # disabled the door open pin
            await self.ezi_io.turn_off_output(self.open_door_pin)
            await asyncio.sleep(0.2)

            # wait for door opening time
            await asyncio.sleep(self.door_open_close_timing_second)

            return True


        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_REQUESTING_OPEN,
                f"Error in handle_requesting_open: {e}"
            )
            return False

    async def handle_requesting_close(self):
        # print("REQUESTING_CLOSE_DOOR")
        # return True

        try:

            await self.ezi_io.turn_off_output(self.open_door_pin)
            await asyncio.sleep(0.2)

           
            await self.ezi_io.turn_on_output(self.close_door_pin)
            await asyncio.sleep(0.2)

            # disabled the door close pin
            await self.ezi_io.turn_off_output(self.close_door_pin)
            await asyncio.sleep(0.2)

            # wait for door closing time
            await asyncio.sleep(self.door_open_close_timing_second)

            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_REQUESTING_CLOSE,
                f"Error in handle_requesting_close: {e}"
            )
            return False

    async def handle_goto_floor(self):
        # print("GOTO Floor")
        # return True
        try:
            await self.ezi_io.turn_on_output(self.floor_pin)
            await asyncio.sleep(0.2)
            # run()이 falsy를 실패로 보므로 성공 경로도 반드시 True를 돌려줘야 한다.
            return True

        except Exception as e:
            self.workflow_result(
                WorkflowState.FAILED,
                False,
                ElevatorError.ERROR_AT_GOTO_FLOOR,
                f"Error in handle_goto_floor: {e}"
            )
            return False
    
    async def handle_waiting_for_elevating(self):
        # print("WAITING_FOR_ELEVATING")
        # return True
        await asyncio.sleep(self.elevating_timing_second)
        return True


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
                ElevatorError.ERROR_AT_UNPAIRING,
                f"Error in handle_unpairing: {e}"
            )
            return False


    async def handle_done(self):
        # print("DONE: Workflow finished")
        # return True
        self.workflow_result(
            WorkflowState.DONE,
            True,
            ElevatorError.NONE,
            ""
        )
        return True

    #--------------------------------
    # Helper functions
    #--------------------------------

    async def is_solid_on(self, pin, required_duration=5.0, poll_interval=0.1, timeout=None):
        """
        Returns True once `pin` has been continuously ON for `required_duration` seconds.
        Resets the streak any time the pin reads OFF (i.e. it's blinking).
        Returns False if `timeout` is given and exceeded before that happens.
        """
        on_since = None
        start_time = time()

        while True:
            out = await self.ezi_io.get_output_pin(pin)

            if out:
                if on_since is None:
                    on_since = time()
                elif time() - on_since >= required_duration:
                    return True
            else:
                on_since = None  # reset streak, it blinked

            if timeout is not None and time() - start_time > timeout:
                return False

            await asyncio.sleep(poll_interval)


    def workflow_result(
        self,
        next_state: WorkflowState,
        result: bool,
        error_state: ElevatorError,
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
                ElevatorError.ERROR_AT_PAIRING,
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
                ElevatorError.ERROR_AT_WORKFLOW_SEQUENCE,
                f"Error in workflow_sequence: {e}"
            )




