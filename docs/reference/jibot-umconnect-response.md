# JIBOT UmConnect Response

Source recording:
`adaptor/logs/jibot/20260605-154519-session.jsonl`

Session:

| Field | Value |
| --- | --- |
| Robot IP | `192.168.3.222` |
| Robot port | `7273` |
| Session ID | `73b646cfe059494bb428faee4fd8095a` |
| Response timestamp | `2026-06-05T06:45:19.623+00:00` |
| Command | `UmConnect` |

## Response Summary

```json
{
  "#CMD#": "UmConnect",
  "msg": "Connect Succeed: login as test, group contains: all",
  "state": true,
  "commandsCount": 66
}
```

## Supported Commands

| Command | Arguments | Return |
| --- | --- | --- |
| `AdvGetParamsRoutes` | `routes:string` | `none` |
| `AdvGetRoutes` | `routes:string` | `none` |
| `AdvGetTemplateRoutes` | `routes:string` | `none` |
| `AdvSetParamsRoutes` | `routes:string` | `none` |
| `AdvSetRoutes` | `routes:string` | `none` |
| `AdvSetTemplateRoutes` | `routes:string` | `none` |
| `AdvTrackManually` | `num,mode` | `none` |
| `GetPolygons` | `-` | `polygons:[{name,points:["x y t",...]},...]` |
| `SetMap` | `name` | `none` |
| `UmConnect` | `-` | `-` |
| `UmDock` | `none` | `none` |
| `UmDrive` | `trans,rot,speed,lat` | `none` |
| `UmGetBatteryInfo` | `none` | `soh, vol, tem1, tem2, tem3` |
| `UmGetClientLog` | `-` | `log` |
| `UmGetConfig` | `none` | `data:{section:{param:{data}}}` |
| `UmGetCurTask` | `none` | `data:{content,routes,key,value,id}` |
| `UmGetInput` | `none` | `num,input array` |
| `UmGetLaser` | `index` | `size,data:{num,points:reading array}` |
| `UmGetLocState` | `none` | `score` |
| `UmGetMap` | `none` | `Type,Objs,Lines,Points,Resolution,NumPoints` |
| `UmGetMapName` | `none` | `name` |
| `UmGetMappingState` | `none` | `state` |
| `UmGetMotorInfo` | `none` | `motorinfo` |
| `UmGetMotorState` | `none` | `flag` |
| `UmGetName` | `none` | `name` |
| `UmGetOnlineBuildMapRes` | `none` | `none` |
| `UmGetOutput` | `none` | `num,output array` |
| `UmGetPath` | `none` | `num,path(array of x,y)` |
| `UmGetPathPlanningClearances` | `none` | `num = 0,array(cur none)` |
| `UmGetPowerOnTime` | `num,mode` | `none` |
| `UmGetRailInfo` | `none` | `rail` |
| `UmGetRobotInfo` | `none` | `x,y,th,vel_f,vel_r,mode,status,battery,station, obs` |
| `UmGetRobotSize` | `none` | `width,length,lengthfront` |
| `UmGetRoutes` | `none` | `data:{routes}` |
| `UmGetSafeDrive` | `none` | `flag:bool` |
| `UmGetTaskInfo` | `none` | `task` |
| `UmGetVirtualIO` | `none` | `value` |
| `UmGoto` | `target,goal,poseX,poseY,poseTh,strict` | `none` |
| `UmIdle` | `none` | `none` |
| `UmLocalize` | `target,goal,poseX,poseY,poseTh` | `none` |
| `UmMapping` | `name,flag,target` | `none` |
| `UmOfflineBuildMap` | `none` | `none` |
| `UmOnlineBuildMap` | `none` | `none` |
| `UmOnlineSaveMap` | `none` | `none` |
| `UmPauseRobot` | `num,mode` | `none` |
| `UmRMSGetConfig` | `none` | `data:{setion:{param:{data}}}` |
| `UmRMSGetMap` | `none` | `Type,Objs,Lines,Points,Resolution,NumPoints` |
| `UmRMSGetMapName` | `none` | `name, md5` |
| `UmRMSGetRoutes` | `none` | `data:{routes}` |
| `UmRMSSetCancelJob` | `section` | `none` |
| `UmRMSSetConfig` | `section objs` | `none` |
| `UmRMSSetMap` | `section` | `none` |
| `UmRMSSetRoutes` | `routes:string` | `none` |
| `UmReloadConfig` | `none` | `none` |
| `UmRoutes` | `routes,key,id` | `none` |
| `UmSchedulerList` | `size,list:array of 'routes:key'` | `none` |
| `UmSchedulerThis` | `routes,key,content` | `none` |
| `UmSetConfig` | `section objs` | `none` |
| `UmSetMotor` | `flag` | `none` |
| `UmSetOutput` | `length,high,low` | `none` |
| `UmSetOutputByte` | `num,flag` | `none` |
| `UmSetRoutes` | `routes:string` | `none` |
| `UmSetSafeDrive` | `flag` | `none` |
| `UmSetVolume` | `volume` | `none` |
| `UmStop` | `none` | `none` |
| `error` | `none` | `level:1-3,device,title,message,suggestion` |

## Full Pretty Payload

```json
{
  "#CMD#": "UmConnect",
  "commands": [
    {
      "arg": "routes:string",
      "cmd": "AdvGetParamsRoutes",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "AdvGetRoutes",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "AdvGetTemplateRoutes",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "AdvSetParamsRoutes",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "AdvSetRoutes",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "AdvSetTemplateRoutes",
      "ret": "none"
    },
    {
      "arg": "num,mode",
      "cmd": "AdvTrackManually",
      "ret": "none"
    },
    {
      "arg": "",
      "cmd": "GetPolygons",
      "ret": "polygons:[{name,points:[\"x y t\",...]},...]"
    },
    {
      "arg": "name",
      "cmd": "SetMap",
      "ret": "none"
    },
    {
      "arg": "",
      "cmd": "UmConnect",
      "ret": ""
    },
    {
      "arg": "none",
      "cmd": "UmDock",
      "ret": "none"
    },
    {
      "arg": "trans,rot,speed,lat",
      "cmd": "UmDrive",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmGetBatteryInfo",
      "ret": "soh, vol, tem1, tem2, tem3"
    },
    {
      "arg": "",
      "cmd": "UmGetClientLog",
      "ret": "log"
    },
    {
      "arg": "none",
      "cmd": "UmGetConfig",
      "ret": "data:{section:{param:{data}}}"
    },
    {
      "arg": "none",
      "cmd": "UmGetCurTask",
      "ret": "data:{content,routes,key,value,id}"
    },
    {
      "arg": "none",
      "cmd": "UmGetInput",
      "ret": "num,input array"
    },
    {
      "arg": "index",
      "cmd": "UmGetLaser",
      "ret": "size,data:{num,points:reading array}"
    },
    {
      "arg": "none",
      "cmd": "UmGetLocState",
      "ret": "score"
    },
    {
      "arg": "none",
      "cmd": "UmGetMap",
      "ret": "Type,Objs,Lines,Points,Resolution,NumPoints"
    },
    {
      "arg": "none",
      "cmd": "UmGetMapName",
      "ret": "name"
    },
    {
      "arg": "none",
      "cmd": "UmGetMappingState",
      "ret": "state"
    },
    {
      "arg": "none",
      "cmd": "UmGetMotorInfo",
      "ret": "motorinfo"
    },
    {
      "arg": "none",
      "cmd": "UmGetMotorState",
      "ret": "flag"
    },
    {
      "arg": "none",
      "cmd": "UmGetName",
      "ret": "name"
    },
    {
      "arg": "none",
      "cmd": "UmGetOnlineBuildMapRes",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmGetOutput",
      "ret": "num,output array"
    },
    {
      "arg": "none",
      "cmd": "UmGetPath",
      "ret": "num,path(array of x,y)"
    },
    {
      "arg": "none",
      "cmd": "UmGetPathPlanningClearances",
      "ret": "num = 0,array(cur none)"
    },
    {
      "arg": "num,mode",
      "cmd": "UmGetPowerOnTime",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmGetRailInfo",
      "ret": "rail"
    },
    {
      "arg": "none",
      "cmd": "UmGetRobotInfo",
      "ret": "x,y,th,vel_f,vel_r,mode,status,battery,station, obs"
    },
    {
      "arg": "none",
      "cmd": "UmGetRobotSize",
      "ret": "width,length,lengthfront"
    },
    {
      "arg": "none",
      "cmd": "UmGetRoutes",
      "ret": "data:{routes}"
    },
    {
      "arg": "none",
      "cmd": "UmGetSafeDrive",
      "ret": "flag:bool"
    },
    {
      "arg": "none",
      "cmd": "UmGetTaskInfo",
      "ret": "task"
    },
    {
      "arg": "none",
      "cmd": "UmGetVirtualIO",
      "ret": "value"
    },
    {
      "arg": "target,goal,poseX,poseY,poseTh,strict",
      "cmd": "UmGoto",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmIdle",
      "ret": "none"
    },
    {
      "arg": "target,goal,poseX,poseY,poseTh",
      "cmd": "UmLocalize",
      "ret": "none"
    },
    {
      "arg": "name,flag,target",
      "cmd": "UmMapping",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmOfflineBuildMap",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmOnlineBuildMap",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmOnlineSaveMap",
      "ret": "none"
    },
    {
      "arg": "num,mode",
      "cmd": "UmPauseRobot",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmRMSGetConfig",
      "ret": "data:{setion:{param:{data}}}"
    },
    {
      "arg": "none",
      "cmd": "UmRMSGetMap",
      "ret": "Type,Objs,Lines,Points,Resolution,NumPoints"
    },
    {
      "arg": "none",
      "cmd": "UmRMSGetMapName",
      "ret": "name, md5"
    },
    {
      "arg": "none",
      "cmd": "UmRMSGetRoutes",
      "ret": "data:{routes}"
    },
    {
      "arg": "section",
      "cmd": "UmRMSSetCancelJob",
      "ret": "none"
    },
    {
      "arg": "section objs",
      "cmd": "UmRMSSetConfig",
      "ret": "none"
    },
    {
      "arg": "section",
      "cmd": "UmRMSSetMap",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "UmRMSSetRoutes",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmReloadConfig",
      "ret": "none"
    },
    {
      "arg": "routes,key,id",
      "cmd": "UmRoutes",
      "ret": "none"
    },
    {
      "arg": "size,list:array of 'routes:key'",
      "cmd": "UmSchedulerList",
      "ret": "none"
    },
    {
      "arg": "routes,key,content",
      "cmd": "UmSchedulerThis",
      "ret": "none"
    },
    {
      "arg": "section objs",
      "cmd": "UmSetConfig",
      "ret": "none"
    },
    {
      "arg": "flag",
      "cmd": "UmSetMotor",
      "ret": "none"
    },
    {
      "arg": "length,high,low",
      "cmd": "UmSetOutput",
      "ret": "none"
    },
    {
      "arg": "num,flag",
      "cmd": "UmSetOutputByte",
      "ret": "none"
    },
    {
      "arg": "routes:string",
      "cmd": "UmSetRoutes",
      "ret": "none"
    },
    {
      "arg": "flag",
      "cmd": "UmSetSafeDrive",
      "ret": "none"
    },
    {
      "arg": "volume",
      "cmd": "UmSetVolume",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "UmStop",
      "ret": "none"
    },
    {
      "arg": "none",
      "cmd": "error",
      "ret": "level:1-3,device,title,message,suggestion"
    }
  ],
  "msg": "Connect Succeed: login as test, group contains: all",
  "state": true
}
```
