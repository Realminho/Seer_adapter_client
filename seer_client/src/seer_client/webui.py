"""Drop-in SEER WebUI assembled from the repository's unmodified WebUI.

Nothing in ``adaptor/`` is patched on disk.  This module creates a SEER
``AdaptorSpec``, keeps the original FileMonitor, and sends operator commands
as complete VDA5050 JSON. By default commands use a localhost TCP transport
to the Adapter for resilience; operators can switch to the production FMS MQTT
transport for end-to-end verification. It also supplies an in-process controller
that launches ``seer_client/run_adapter.py``.
"""

from __future__ import annotations

import os
import json
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from types import MethodType
from typing import Callable, Mapping, Optional, Sequence, Tuple

from .drive_limits import ManualDriveLimits
from .fleet import SeerFleetError, SeerRobotConfig, load_seer_fleet, validate_seer_fleet
from .io_cache import SeerIOCache
from .jack_status import read_jack_status
from .runtime_mailbox import atomic_write_json, read_bytes_locked, read_bytes_snapshot, read_json_locked, read_json_snapshot
from .status import read_controller_status_cache
from .map_view import (
    SEER_MAP_INTERACTION_JS,
    clear_active_route,
    read_map_cache,
    render_fleet_map_card,
    render_map_card,
    write_active_route,
)
from .recipe_builder import (
    RecipeBuilderError,
    delete_recipe,
    managed_recipe_names,
    redirect_result,
    render_builder_page,
    save_recipe,
)
from .platform_compat import install_fcntl_compat
from .vda5050_console import Vda5050Identity
from .vda5050_order import build_route_order, build_vda_action
from .vda5050_trace import (
    Vda5050TraceStore,
    Vda5050WebSender,
    render_vda5050_page,
)


install_fcntl_compat()


SEER_PACKAGE_DIR = Path(__file__).resolve().parent
SEER_CLIENT_ROOT = SEER_PACKAGE_DIR.parents[1]
REPO_ROOT = SEER_CLIENT_ROOT.parent
ADAPTOR_ROOT = REPO_ROOT / "adaptor"
RUN_ADAPTER = SEER_CLIENT_ROOT / "run_adapter.py"
PRINT_LOG = SEER_CLIENT_ROOT / "print_log.py"


_SEER_MAP_VIEWS = {}
_SEER_IO_VIEWS = {}
_SEER_RECIPE_ACTION_TYPES = {}
_RECIPE_ACTION_COUNTER_LOCK = threading.Lock()
_RECIPE_ACTION_COUNTER_FILE = "recipe-action-counts.json"


def _reserve_recipe_action_id(recipe_path: Path, recipe_name: str) -> str:
    """Return ``<Recipe>-<execution count>`` and persist the next count.

    The counter lives beside that AMR's member-local ``recipes.hcl`` so two
    robots running a Recipe with the same name do not share execution counts.
    Counter reservation happens before VDA5050 delivery to keep Action IDs
    unique even if a transport error occurs after the command was accepted.
    """

    name = str(recipe_name or "").strip()
    if not name:
        raise ValueError("Recipe name is required for Action ID allocation")
    counter_path = Path(recipe_path).parent / _RECIPE_ACTION_COUNTER_FILE
    with _RECIPE_ACTION_COUNTER_LOCK:
        counts = {}
        try:
            raw = json.loads(counter_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("counts"), dict):
                counts = dict(raw["counts"])
        except (OSError, ValueError, json.JSONDecodeError):
            counts = {}
        try:
            previous = max(0, int(counts.get(name, 0) or 0))
        except (TypeError, ValueError):
            previous = 0
        execution_count = previous + 1
        counts[name] = execution_count
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = counter_path.with_suffix(counter_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"schema": 1, "counts": counts}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(counter_path)
    return f"{name}-{execution_count:03d}"


def _wait_tcp_ready(host: str, port: int, timeout: float = 8.0) -> bool:
    """Wait until one AMR Adapter localhost control socket is accepting TCP.

    Recipe save/delete restarts only the selected Adapter.  Returning control to
    the Builder before that socket is listening creates a race where the user
    presses Run and the command is lost as "adapter offline".
    """

    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((str(host), int(port)), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.10)
    return False


def _send_selected_builder_action(
    web,
    robot_key: str,
    action_type: str,
    *,
    source_user: str = "seer",
    parameters: Optional[Mapping[str, object]] = None,
) -> Tuple[bool, str]:
    """Send a Block Builder control to exactly one selected AMR.

    This deliberately bypasses the generic dashboard action lookup.  The
    Builder owns an AMR-specific recipes.hcl, so its Run button must validate
    that exact file and then use that AMR's own VDA factory + sender.  This
    prevents a stale dashboard spec or another AMR's recipe registry from
    hijacking the Builder execution target.
    """

    key = str(robot_key or "").strip()
    action = str(action_type or "").strip()
    if not key:
        return False, "실행할 SEER AMR을 선택하세요."
    sender = (getattr(web, "_seer_vda_senders", {}) or {}).get(key)
    trace = (getattr(web, "_seer_vda_traces", {}) or {}).get(key)
    if sender is None or trace is None:
        return False, "선택한 AMR의 VDA5050 전송 채널이 없습니다."

    builder_controls = {
        "startPause",
        "stopPause",
        "seerCancelActiveAction",
        "seerResetActionErrors",
    }
    recipe_path = None
    if action not in builder_controls:
        recipe_path = (getattr(web, "_seer_recipe_paths", {}) or {}).get(key)
        if recipe_path is None:
            return False, "선택한 AMR의 Recipe 파일을 찾을 수 없습니다."
        try:
            names = managed_recipe_names(Path(recipe_path).read_text(encoding="utf-8"))
        except OSError as exc:
            return False, f"Recipe 파일을 읽을 수 없습니다: {exc}"
        if action not in names:
            return False, f"{action} Recipe는 선택한 AMR에 저장되어 있지 않습니다."

    # Local VDA5050 is the normal Builder transport.  A Recipe save/delete can
    # restart only this member, so wait for that exact member's control socket
    # before sending once.  We do not retry after send() because a timeout after
    # delivery could otherwise execute a motion Action twice.
    if getattr(sender, "mode", "local") == "local":
        local = getattr(sender, "local_sender", None)
        host = str(getattr(local, "host", "127.0.0.1") or "127.0.0.1")
        port = int(getattr(local, "port", 0) or 0)
        if port <= 0 or not _wait_tcp_ready(host, port, timeout=8.0):
            return False, "선택한 AMR Adapter 제어 채널이 아직 준비되지 않았습니다."

    action_id = (
        _reserve_recipe_action_id(Path(recipe_path), action)
        if recipe_path is not None
        else None
    )
    payload = trace.factory.instant_action(
        action,
        parameters,
        action_id=action_id,
    )
    delivered, message = sender.send(
        payload,
        {
            "source_user": str(source_user or "seer"),
            "confirmed": True,
            "source": "block-builder",
            "created_at": time.time(),
        },
    )
    return bool(delivered), str(message or ("delivered" if delivered else "rejected"))


def _read_builder_runtime(robot_key: str) -> dict:
    runtime_path = _SEER_MAP_VIEWS.get(str(robot_key or ""), {}).get("block_runtime_path")
    if runtime_path is None:
        return {}
    try:
        value = json.loads(Path(runtime_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _load_amr_nicknames(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for ip, nickname in raw.items():
        ip_text = str(ip or "").strip()
        name_text = str(nickname or "").strip()
        if ip_text and name_text:
            result[ip_text] = name_text[:40]
    return result


def _save_amr_nicknames(path: Path, nicknames: dict[str, str]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        str(ip): str(name)[:40]
        for ip, name in sorted(nicknames.items())
        if str(ip).strip() and str(name).strip()
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)


def _seer_nickname_ip_label(spec) -> str:
    """Operator-facing AMR label: nickname/display name plus the actual controller IP."""

    nickname = str(
        getattr(spec, "display_name", "")
        or getattr(spec, "serial", "")
        or "SEER"
    ).strip()
    ip = str(getattr(spec, "vehicle_host", "") or "").strip()
    if ip and ip not in nickname:
        return f"{nickname} - {ip}"
    return nickname or ip or "SEER"


def _build_vda_trace(config, runtime_dir: Path) -> Vda5050TraceStore:
    return Vda5050TraceStore(
        Vda5050Identity(
            serial_number=str(config.vehicle.serial_number),
            manufacturer=str(config.vehicle.manufacturer),
            interface=str(config.mqtt_broker.vda_interface),
            topic_version=str(config.vehicle.vda_version),
            message_version=str(config.vehicle.vda_full_version),
        ),
        host=str(config.mqtt_broker.host),
        port=int(config.mqtt_broker.port),
        history_path=Path(runtime_dir) / "vda5050-trace.jsonl",
    )


_SEER_AWARE_JOG_JS = """
<script>
(function(){
  if(window.__amrJog) return; window.__amrJog=true;
  var beat=null, pressed={}, pointerDir=null, moving=false, lastPayloadKey='', inFlight=false, queued=null, lastSettingsRoot=null;
  var armLatched=false, armIdleTimer=null, ARM_IDLE_MS=30000, HEARTBEAT_MS=250, drainWaiters=[];
  function root(){ return document.querySelector('.manual'); }
  function speedStorageKey(r){ return 'seer-manual-speed:'+String((r&&r.getAttribute('data-key'))||'default'); }
  function armInput(r){ return r&&r.querySelector('input[name="manual_armed"]'); }
  function armed(){ var r=root(), input=armInput(r); return !!(input&&input.checked&&armLatched); }
  function clearArmIdleTimer(){ if(armIdleTimer){clearTimeout(armIdleTimer);armIdleTimer=null;} }
  function syncArmUi(r){
    if(!r) return;
    var input=armInput(r), enabled=!!(input&&input.checked&&armLatched);
    if(input&&input.checked!==enabled) input.checked=enabled;
    r.classList.toggle('is-jog-armed',enabled);
    r.querySelectorAll('.jog-btn[data-dir]').forEach(function(button){button.disabled=!enabled;});
    var label=r.querySelector('[data-jog-arm-state]');
    if(label) label.textContent=enabled?'수동조작 ON':'수동조작 OFF';
  }
  function restoreArmState(r){
    var input=armInput(r); if(!input) return;
    input.checked=!!armLatched;
    syncArmUi(r);
  }
  function scheduleArmIdle(){
    clearArmIdleTimer();
    if(!armLatched||moving||Object.keys(pressed).length) return;
    armIdleTimer=setTimeout(function(){disarmManual('idle');},ARM_IDLE_MS);
  }
  function touchArmActivity(){ if(armLatched) scheduleArmIdle(); }
  function disarmManual(_reason){
    clearArmIdleTimer();
    stopAll();
    armLatched=false;
    var r=root(), input=armInput(r);
    if(input) input.checked=false;
    syncArmUi(r);
  }
  function saveSpeedValues(r){
    if(!r||r.getAttribute('data-seer')!=='1') return;
    var linear=r.querySelector('input[name="linear_speed"]');
    var angular=r.querySelector('input[name="angular_speed_deg"]');
    if(!linear||!angular) return;
    /* Save the raw strings too. An empty field is a valid editing state and
       must not be replaced by the previous value during a live-card refresh. */
    try{ localStorage.setItem(speedStorageKey(r),JSON.stringify({linear:String(linear.value),angular:String(angular.value)})); }catch(_error){}
  }
  function restoreSpeedValues(r){
    if(!r||r.getAttribute('data-seer')!=='1') return;
    var linear=r.querySelector('input[name="linear_speed"]');
    var angular=r.querySelector('input[name="angular_speed_deg"]');
    if(!linear||!angular) return;
    if(document.activeElement===linear||document.activeElement===angular) return;
    try{
      var raw=localStorage.getItem(speedStorageKey(r)); if(!raw) return;
      var saved=JSON.parse(raw)||{};
      if(typeof saved.linear==='string'||typeof saved.linear==='number') linear.value=String(saved.linear);
      if(typeof saved.angular==='string'||typeof saved.angular==='number') angular.value=String(saved.angular);
    }catch(_error){}
  }
  function restoreCurrentSettings(){ var r=root(); if(!r) return; lastSettingsRoot=r; restoreArmState(r); restoreSpeedValues(r); }
  function finiteInput(el, low, high){
    if(!el||String(el.value).trim()==='') return null;
    var value=parseFloat(el.value); if(!Number.isFinite(value)) return null;
    return Math.max(low,Math.min(high,value));
  }
  function rawSend(job){
    if(!job) return Promise.resolve();
    if(job.type!=='manualStop'&&!armed()) return Promise.resolve();
    var b=new URLSearchParams();
    b.set('csrf_token',job.csrf||''); b.set('action_type',job.type); b.set('confirm','on');
    b.set('armed','on');
    if(job.extra){ for(var k in job.extra) b.set(k,job.extra[k]); }
    return fetch('/adapter/'+encodeURIComponent(job.key||'').replace(/%3A/gi, ':')+'/manual',
      {method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:b.toString()}).then(function(response){
        if(!response.ok) throw new Error('manual control HTTP '+response.status);
        return response;
      });
  }
  function resolveDrainWaiters(){
    if(inFlight||queued) return;
    var waiters=drainWaiters.splice(0,drainWaiters.length);
    waiters.forEach(function(resolve){try{resolve();}catch(_error){}});
  }
  function flushSend(){
    if(inFlight||!queued){resolveDrainWaiters();return;}
    var job=queued; queued=null; inFlight=true;
    Promise.resolve(rawSend(job)).catch(function(error){
      try{console.warn('[SEER manual control]',error);}catch(_ignore){}
    }).finally(function(){
      inFlight=false; if(queued) flushSend(); else resolveDrainWaiters();
    });
  }
  function send(type,extra){
    var r=root(); if(!r) return;
    var c=document.getElementById('mc-csrf');
    queued={
      key:String(r.getAttribute('data-key')||''),
      csrf:c?String(c.value||''):'',
      type:type,
      extra:extra||null
    };
    flushSend();
  }
  function drainSends(){
    if(!inFlight&&!queued) return Promise.resolve();
    return new Promise(function(resolve){drainWaiters.push(resolve);});
  }
  function speedValues(){
    var r=root(); if(!r||!armed()) return null;
    var isSeer=r.getAttribute('data-seer')==='1';
    var TRANS=parseFloat(r.getAttribute('data-trans'))||(isSeer?0.05:200);
    var ROT=parseFloat(r.getAttribute('data-rot'))||(isSeer?5:30);
    var SPEED=parseFloat(r.getAttribute('data-speed'))||(isSeer?0.05:200);
    if(isSeer){
      var linear=r.querySelector('input[name="linear_speed"]');
      var angular=r.querySelector('input[name="angular_speed_deg"]');
      TRANS=finiteInput(linear,parseFloat(linear.min)||0.01,parseFloat(linear.max)||0.5);
      ROT=finiteInput(angular,parseFloat(angular.min)||1,parseFloat(angular.max)||60);
      if(TRANS===null||ROT===null) return null;
      SPEED=TRANS;
    }else{
      var speedEl=r.querySelector('input[name="speed"]'); SPEED=parseFloat(speedEl&&speedEl.value)||SPEED;
    }
    return {trans:TRANS,rot:ROT,speed:SPEED};
  }
  function desiredPayload(){
    var values=speedValues(); if(!values) return null;
    var forward=(pressed.up?1:0)-(pressed.down?1:0);
    var turn=(pressed.left?1:0)-(pressed.right?1:0);
    return {trans:forward*values.trans,rot:turn*values.rot,speed:values.speed,lat:0};
  }
  function ensureBeat(){ if(beat) return; beat=setInterval(function(){ if(moving) applyMotion(true); },HEARTBEAT_MS); }
  function clearBeat(){ if(beat){clearInterval(beat);beat=null;} }
  function applyMotion(force){
    var payload=desiredPayload();
    if(!payload){ if(moving) stopAll(); return; }
    var active=Math.abs(payload.trans)>1e-12||Math.abs(payload.rot)>1e-12||Math.abs(payload.lat)>1e-12;
    if(!active){
      if(moving){ send('manualStop'); moving=false; lastPayloadKey=''; }
      clearBeat(); scheduleArmIdle(); return;
    }
    clearArmIdleTimer();
    var key=[payload.trans,payload.rot,payload.speed,payload.lat].join('|');
    if(force||!moving||key!==lastPayloadKey) send('manualDrive',payload);
    moving=true; lastPayloadKey=key; ensureBeat();
  }
  function pressDir(d){ if(!d||!armed()) return; touchArmActivity(); pressed[d]=true; applyMotion(false); }
  function releaseDir(d){ if(!d) return; delete pressed[d]; applyMotion(false); }
  function stopAll(){
    pressed={}; pointerDir=null; clearBeat();
    if(moving||lastPayloadKey){ send('manualStop'); }
    moving=false; lastPayloadKey='';
  }
  function beforeAutonomousCommand(){
    var hadManualSession=!!(armLatched||moving||lastPayloadKey||inFlight||queued||Object.keys(pressed).length);
    if(!hadManualSession) return Promise.resolve();
    clearArmIdleTimer();
    pressed={}; pointerDir=null; clearBeat(); moving=false; lastPayloadKey='';
    armLatched=false;
    var r=root(), input=armInput(r); if(input) input.checked=false; syncArmUi(r);
    /* Always put one final manualStop after the newest heartbeat, then wait for
       its HTTP/local-control acknowledgement before an autonomous order may be
       submitted. This prevents a late stop/watchdog from cancelling the new
       Path Nav task on a slow SEER link. */
    send('manualStop');
    return drainSends();
  }
  window.__seerBeforeAutonomousCommand=beforeAutonomousCommand;
  function dirOf(t){var el=t&&t.closest?t.closest('.jog-btn[data-dir]'):null;return el?el.getAttribute('data-dir'):null;}
  document.addEventListener('mousedown',function(e){var d=dirOf(e.target);if(d){e.preventDefault();pointerDir=d;pressDir(d);}});
  document.addEventListener('mouseup',function(){if(pointerDir){var d=pointerDir;pointerDir=null;releaseDir(d);}});
  document.addEventListener('touchstart',function(e){var d=dirOf(e.target);if(d){e.preventDefault();pointerDir=d;pressDir(d);}},{passive:false});
  document.addEventListener('touchend',function(){if(pointerDir){var d=pointerDir;pointerDir=null;releaseDir(d);}});
  document.addEventListener('click',function(e){if(e.target.closest&&e.target.closest('.jog-stop')){e.preventDefault();touchArmActivity();stopAll();}});
  document.addEventListener('input',function(e){
    var t=e.target;
    if(t&&t.matches&&t.matches('.manual input[name="linear_speed"],.manual input[name="angular_speed_deg"]')){saveSpeedValues(t.closest('.manual'));touchArmActivity();}
  });
  document.addEventListener('change',function(e){
    var t=e.target;
    if(t&&t.matches&&t.matches('.manual input[name="manual_armed"]')){
      var r=t.closest('.manual');
      armLatched=!!t.checked;
      if(armLatched) scheduleArmIdle(); else {clearArmIdleTimer();stopAll();}
      syncArmUi(r); return;
    }
    if(t&&t.matches&&t.matches('.manual input[name="linear_speed"],.manual input[name="angular_speed_deg"]')){saveSpeedValues(t.closest('.manual'));touchArmActivity();}
  });
  document.addEventListener('submit',function(e){
    var form=e.target;
    if(!form||!form.querySelector) return;
    var action=form.querySelector('input[name="action_type"]');
    if(!action||action.value!=='vdaOrderRoute') return;
    if(form.dataset.seerManualHandoffReady==='1'){
      delete form.dataset.seerManualHandoffReady;
      return;
    }
    var hadManualSession=!!(armLatched||moving||lastPayloadKey||inFlight||queued||Object.keys(pressed).length);
    if(!hadManualSession) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    beforeAutonomousCommand().then(function(){
      form.dataset.seerManualHandoffReady='1';
      if(typeof form.requestSubmit==='function') form.requestSubmit();
      else form.submit();
    }).catch(function(error){
      try{console.warn('[SEER manual handoff]',error);}catch(_ignore){}
    });
  },true);
  var keys={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right'};
  document.addEventListener('keydown',function(e){var d=keys[e.key];if(d&&root()&&armed()){e.preventDefault();touchArmActivity();if(!pressed[d]) pressDir(d);}});
  document.addEventListener('keyup',function(e){var d=keys[e.key];if(d&&pressed[d]){e.preventDefault();releaseDir(d);}});
  /* Switching browser focus/tabs must not stop an AMR.  Only an actual page
     unload disarms manual control; keyup/STOP remain the normal immediate stop. */
  window.addEventListener('pagehide',function(){disarmManual('pagehide');});
  restoreCurrentSettings();
  if(window.MutationObserver){
    new MutationObserver(function(){
      var current=root();
      /* Live dashboard refresh may replace the manual-control card. Keep the
         in-memory arm latch only while the user stays on this same page. */
      if(current&&current!==lastSettingsRoot) restoreCurrentSettings();
    }).observe(document.documentElement,{childList:true,subtree:true});
  }
})();
</script>
"""

_SEER_EMERGENCY_INTERACTION_JS = r"""
(function(){
  if(window.__seerEmergencyUiInstalled) return;
  window.__seerEmergencyUiInstalled=true;
  function emergencyForm(target){
    return target&&target.closest?target.closest('form[data-seer-estop-form]'):null;
  }
  function restoreViewport(x,y){
    window.requestAnimationFrame(function(){
      window.scrollTo(x,y);
      window.requestAnimationFrame(function(){window.scrollTo(x,y);});
    });
  }
  document.addEventListener('submit',function(event){
    var form=emergencyForm(event.target);
    if(!form) return;
    event.preventDefault();
    event.stopPropagation();
    if(window.__seerEmergencyPending) return;
    window.__seerEmergencyPending=true;
    var button=form.querySelector('button[type="submit"]');
    var help=form.querySelector('.estop-help');
    var scrollX=window.scrollX, scrollY=window.scrollY;
    if(button){button.disabled=true;button.setAttribute('aria-busy','true');}
    var body=new URLSearchParams();
    new FormData(form).forEach(function(value,name){body.append(name,String(value));});
    fetch(form.action,{method:'POST',credentials:'same-origin',redirect:'follow',cache:'no-store',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()})
      .then(function(response){
        if(!response.ok) throw new Error('HTTP '+response.status);
        return response.text();
      })
      .then(function(html){
        var nextDocument=new DOMParser().parseFromString(html,'text/html');
        var current=document.getElementById('content');
        var next=nextDocument.getElementById('content');
        if(!current||!next) throw new Error('응답 화면을 읽을 수 없습니다.');
        Array.prototype.slice.call(next.children).forEach(function(child){
          if(child.tagName==='P'&&child.classList.contains('ok')) child.remove();
        });
        current.innerHTML=next.innerHTML;
        restoreViewport(scrollX,scrollY);
      })
      .catch(function(error){
        var message='비상정지 요청 실패 · '+String(error.message||error);
        if(help){help.textContent=message;help.style.color='#ff7b86';}
        if(button){button.title=message;}
        restoreViewport(scrollX,scrollY);
      })
      .finally(function(){
        window.__seerEmergencyPending=false;
        if(button&&document.contains(button)){
          button.disabled=false;button.removeAttribute('aria-busy');
        }
      });
  });
})();
"""


_SEER_PRESERVE_FORM_INTERACTION_JS = r"""
(function(){
  if(window.__seerPreserveFormsInstalled) return;
  window.__seerPreserveFormsInstalled=true;
  function managedForm(target){
    var form=target&&target.closest?target.closest('form'):null;
    if(!form||!form.closest('#content')) return null;
    if(String(form.getAttribute('method')||'get').toLowerCase()!=='post') return null;
    if(form.matches('[data-seer-estop-form],[data-seer-map-nav-form],[data-seer-builder-save-form],[data-seer-builder-delete-form]')) return null;
    if(form.target&&form.target.toLowerCase()!=='_self') return null;
    return form;
  }
  function restoreViewport(x,y){
    window.requestAnimationFrame(function(){
      window.scrollTo(x,y);
      window.requestAnimationFrame(function(){window.scrollTo(x,y);});
    });
  }
  function toast(message,isError){
    if(!message) return;
    var old=document.getElementById('seer-action-toast');
    if(old) old.remove();
    var notice=document.createElement('div');
    notice.id='seer-action-toast';
    notice.setAttribute('role',isError?'alert':'status');
    notice.textContent=message;
    notice.style.cssText='position:fixed;right:18px;bottom:18px;z-index:3000;'+
      'max-width:min(420px,calc(100vw - 36px));padding:11px 14px;border-radius:10px;'+
      'box-shadow:0 10px 30px rgba(0,0,0,.35);font-weight:700;'+
      (isError?'background:#b4232f;color:#fff;border:1px solid #ff7b86;':
        'background:#13795b;color:#fff;border:1px solid #46d7a4;');
    document.body.appendChild(notice);
    window.setTimeout(function(){if(notice.parentNode)notice.remove();},2600);
  }
  function detachFlash(next){
    var result={message:'',isError:false};
    Array.prototype.slice.call(next.children).forEach(function(child){
      var success=child.tagName==='P'&&child.classList.contains('ok');
      var failure=child.classList&&child.classList.contains('error-notice');
      if(!success&&!failure) return;
      if(!result.message) result.message=String(child.textContent||'').trim();
      result.isError=result.isError||failure;
      child.remove();
    });
    return result;
  }
  document.addEventListener('submit',function(event){
    var form=managedForm(event.target);
    if(!form) return;
    event.preventDefault();
    if(window.__seerFormPending||form.dataset.seerSubmitting==='1') return;
    window.__seerFormPending=true;
    form.dataset.seerSubmitting='1';
    var submitter=event.submitter||null;
    var controls=Array.prototype.slice.call(
      form.querySelectorAll('button,input[type="submit"],input[type="image"]'));
    var scrollX=window.scrollX, scrollY=window.scrollY;
    controls.forEach(function(control){control.disabled=true;control.setAttribute('aria-busy','true');});
    var body=new URLSearchParams();
    new FormData(form).forEach(function(value,name){body.append(name,String(value));});
    if(submitter&&submitter.name&&!body.has(submitter.name)){
      body.append(submitter.name,String(submitter.value||''));
    }
    fetch(form.action,{method:'POST',credentials:'same-origin',redirect:'follow',cache:'no-store',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()})
      .then(function(response){
        if(!response.ok) throw new Error('HTTP '+response.status);
        return response.text();
      })
      .then(function(html){
        var nextDocument=new DOMParser().parseFromString(html,'text/html');
        var current=document.getElementById('content');
        var next=nextDocument.getElementById('content');
        if(!current||!next) throw new Error('응답 화면을 읽을 수 없습니다.');
        var feedback=detachFlash(next);
        current.innerHTML=next.innerHTML;
        restoreViewport(scrollX,scrollY);
        toast(feedback.message,feedback.isError);
      })
      .catch(function(error){
        toast('요청 실패 · '+String(error.message||error),true);
        restoreViewport(scrollX,scrollY);
      })
      .finally(function(){
        window.__seerFormPending=false;
        if(document.contains(form)) delete form.dataset.seerSubmitting;
        controls.forEach(function(control){
          if(!document.contains(control)) return;
          control.disabled=false;control.removeAttribute('aria-busy');
        });
      });
  });
  var initialContent=document.getElementById('content');
  if(initialContent){
    var initialFeedback=detachFlash(initialContent);
    toast(initialFeedback.message,initialFeedback.isError);
  }
  try{
    var cleanUrl=new URL(window.location.href);
    var changed=false;
    ['msg','err','act'].forEach(function(name){
      if(cleanUrl.searchParams.has(name)){cleanUrl.searchParams.delete(name);changed=true;}
    });
    if(changed) window.history.replaceState(null,'',cleanUrl);
  }catch(error){}
})();
"""


_SEER_IO_STYLE = """
<style>
.seer-io-page{display:grid;gap:16px}
.seer-io-help{display:flex;gap:14px;align-items:center;flex-wrap:wrap;color:var(--muted)}
.seer-io-legend{display:inline-flex;align-items:center;gap:6px}
.seer-io-dot{width:18px;height:18px;border-radius:50%;display:inline-block;background:#ef4444;box-shadow:inset 0 0 0 1px rgba(0,0,0,.1)}
.seer-io-dot.high{background:#16a34a}.seer-io-dot.invalid{background:#9ca3af}
.seer-io-section{display:grid;gap:10px}
.seer-io-section+.seer-io-section{margin-top:10px}
.seer-io-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:9px}
.seer-io-channel{min-width:0;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--muted-surface);padding:10px;display:grid;gap:8px}
.seer-io-channel-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.seer-io-name{font-family:var(--font-mono);font-weight:700}.seer-io-source{color:var(--muted);font-size:11px}
.seer-io-value{display:flex;align-items:center;gap:8px;font-family:var(--font-mono);font-weight:700}
.seer-io-switch{min-width:78px;border:0;border-radius:999px;padding:3px 7px;background:#e5e7eb;color:#374151;display:inline-flex;align-items:center;justify-content:space-between;gap:7px}
.seer-io-switch.on{background:#0ea5e9;color:#fff}.seer-io-switch .thumb{width:22px;height:22px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.28);order:-1}.seer-io-switch.on .thumb{order:1}
.seer-io-empty{padding:18px;border:1px dashed var(--border);border-radius:var(--radius-md);color:var(--muted)}
@media(max-width:640px){.seer-io-grid{grid-template-columns:1fr 1fr}.seer-io-channel{padding:8px}}
@media(max-width:430px){.seer-io-grid{grid-template-columns:1fr}}
</style>
"""


def _seer_io_channel_cards(
    render,
    *,
    kind: str,
    channels,
    spec_key: str,
    csrf: str,
    return_to: str,
) -> str:
    cards = []
    for channel in channels or ():
        if not isinstance(channel, dict):
            continue
        channel_id = channel.get("id", "?")
        source = str(channel.get("source", "normal") or "normal")
        valid = bool(channel.get("valid", True))
        status = bool(channel.get("status", False))
        dot_class = "invalid" if not valid else ("high" if status else "")
        value = "1" if status else "0"
        if kind == "DO" and valid:
            target = "false" if status else "true"
            switch_class = " on" if status else ""
            switch_text = "ON" if status else "OFF"
            control = (
                f'<form method="post" action="/adapter/{render.esc(spec_key)}/action">'
                f'{render._csrf_field(csrf)}{render._return_to_field(return_to)}'
                '<input type="hidden" name="action_type" value="seerSetDO">'
                f'<input type="hidden" name="id" value="{render.esc(channel_id)}">'
                f'<input type="hidden" name="status" value="{target}">'
                f'<button class="seer-io-switch{switch_class}" type="submit" role="switch" '
                f'aria-checked="{str(status).lower()}" aria-label="DO {render.esc(channel_id)} {switch_text}; 누르면 전환">'
                f'<span>{switch_text}</span><span class="thumb"></span></button></form>'
            )
        else:
            control = f'<span>{value}</span>' if valid else '<span>invalid</span>'
        cards.append(
            '<article class="seer-io-channel">'
            '<div class="seer-io-channel-head">'
            f'<span class="seer-io-name">{kind}{render.esc(channel_id)}</span>'
            f'<span class="seer-io-source">{render.esc(source)}</span></div>'
            f'<div class="seer-io-value"><span class="seer-io-dot {dot_class}"></span>'
            f'{control}</div></article>'
        )
    return (
        '<div class="seer-io-grid">' + "".join(cards) + "</div>"
        if cards
        else '<p class="seer-io-empty">채널 데이터가 없습니다.</p>'
    )


def _render_seer_io_page(render, specs, csrf: str, q: dict) -> str:
    try:
        refresh = max(0, int(q.get("refresh", "1")))
    except (TypeError, ValueError):
        refresh = 1

    specs = list(specs or ())
    selected_key = str(q.get("robot", "") or "").strip()
    selected_spec = next(
        (spec for spec in specs if str(getattr(spec, "key", "")) == selected_key),
        specs[0] if specs else None,
    )

    selector = ""
    if specs:
        links = []
        for spec in specs:
            key = str(getattr(spec, "key", "") or "")
            params = {"robot": key, "refresh": str(refresh)}
            href = "/io?" + urllib.parse.urlencode(params)
            selected = selected_spec is not None and key == selected_spec.key
            cls = "seer-io-robot is-current" if selected else "seer-io-robot"
            label = _seer_nickname_ip_label(spec)
            links.append(
                f'<a class="{cls}" href="{render.esc(href)}" '
                f'data-seer-amr-label-key="{render.esc(key)}" '
                f'aria-current="{"page" if selected else "false"}">'
                f'{render.esc(label)}</a>'
            )
        selector = (
            '<section class="panel seer-io-selector"><div class="panel-head"><div>'
            '<h2>AMR 선택</h2><p class="panel-desc">선택한 AMR의 DI/DO만 표시합니다.</p>'
            '</div></div><div class="panel-body"><nav class="seer-io-robot-list" '
            'aria-label="I/O AMR 선택">' + "".join(links) + "</nav></div></section>"
        )

    if selected_spec is None:
        robot_panel = '<p class="seer-io-empty">등록된 SEER AMR이 없습니다.</p>'
    else:
        spec = selected_spec
        return_to = (
            "/io?" + urllib.parse.urlencode(
                {"robot": str(spec.key), "refresh": str(refresh)}
            )
        )
        cache_path = _SEER_IO_VIEWS.get(getattr(spec, "key", ""))
        snapshot = SeerIOCache.read(cache_path) if cache_path is not None else None
        if snapshot is None:
            body = '<p class="seer-io-empty">API 1013 상태를 기다리는 중입니다.</p>'
            status_pill = '<span class="status warning">WAITING</span>'
        else:
            now = time.time()
            try:
                age = max(0.0, now - float(snapshot.get("updated_at", now)))
            except (TypeError, ValueError):
                age = 0.0
            fresh = age <= 3.0
            status_pill = (
                f'<span class="status {"success" if fresh else "warning"}">'
                f'{"LIVE" if fresh else "STALE"} · {age:.1f}s</span>'
            )
            error = str(snapshot.get("err_msg", "") or "")
            notice = (
                f'<p class="err">API 1013: {render.esc(error)}</p>' if error else ""
            )
            di_cards = _seer_io_channel_cards(
                render,
                kind="DI",
                channels=snapshot.get("DI", []),
                spec_key=spec.key,
                csrf=csrf,
                return_to=return_to,
            )
            do_cards = _seer_io_channel_cards(
                render,
                kind="DO",
                channels=snapshot.get("DO", []),
                spec_key=spec.key,
                csrf=csrf,
                return_to=return_to,
            )
            body = (
                f'{notice}<div class="seer-io-section"><h3>DI · Digital Input (조회 전용)</h3>'
                f'{di_cards}</div><div class="seer-io-section">'
                '<h3>DO · Digital Output (API 6001 ON/OFF)</h3>'
                f'{do_cards}</div>'
            )
        robot_panel = (
            '<section class="panel"><div class="panel-head"><div>'
            f'<h2 data-seer-amr-label-key="{render.esc(str(spec.key))}">{render.esc(_seer_nickname_ip_label(spec))}</h2>'
            f'<p class="panel-desc">{render.esc(spec.serial)} · 상태 API 1013</p>'
            f'</div>{status_pill}</div><div class="panel-body">{body}</div></section>'
        )

    extra_style = """
<style id="seer-io-selector-style">
.seer-io-robot-list{display:flex;gap:8px;flex-wrap:wrap}
.seer-io-robot{display:inline-flex;align-items:center;min-height:36px;padding:7px 12px;
 border:1px solid var(--border);border-radius:10px;background:var(--muted-surface);
 color:var(--text);font-weight:700;text-decoration:none}
.seer-io-robot:hover{border-color:#5aa8d6;background:#eef7ff}
.seer-io-robot.is-current{border-color:#1f78d1;background:#dbeeff;color:#0b3558!important;
 box-shadow:inset 0 0 0 1px rgba(31,120,209,.18)}
</style>
"""
    content = (
        f'{render._flash(q)}{_SEER_IO_STYLE}{extra_style}<div class="seer-io-page">'
        '<div><h1>SEER I/O</h1><p class="subtitle">DI는 장비 입력 상태이며 조회만 가능합니다. '
        'DO 스위치는 즉시 실제 출력 API 6001을 호출합니다.</p></div>'
        '<div class="seer-io-help">'
        '<span class="seer-io-legend"><span class="seer-io-dot high"></span>High · 1</span>'
        '<span class="seer-io-legend"><span class="seer-io-dot"></span>Low · 0</span>'
        '<span class="seer-io-legend"><span class="seer-io-dot invalid"></span>Invalid</span></div>'
        + selector + robot_panel + "</div>"
        + """<script id="seer-io-amr-label-sync">
(function(){async function sync(){try{var r=await fetch('/seer/amr-labels?_t='+Date.now(),{cache:'no-store'});if(!r.ok)return;var p=await r.json(),labels=(p&&p.labels)||{};document.querySelectorAll('[data-seer-amr-label-key]').forEach(function(n){var k=n.getAttribute('data-seer-amr-label-key');if(k&&labels[k])n.textContent=labels[k];});}catch(_error){}}sync();window.setInterval(sync,1000);})();
</script>"""
    )
    brand_title = (
        f"SEER I/O · {_seer_nickname_ip_label(selected_spec)}"
        if selected_spec is not None else "SEER I/O"
    )
    return render.page(
        "SEER I/O",
        render._with_poll(content, refresh),
        current="/io",
        brand_title=brand_title,
    )


def _seer_file_monitor(serial: str):
    """Build a SEER view whose ONLINE flag means *vehicle* link health.

    The shared :class:`FileMonitor` calls a fresh ``state.json`` "online" even
    when the controller is unreachable, because that property means only that
    the Adapter process is alive.  SEER has an independent MQTT/broker link, so
    an Adapter can keep writing state while reporting ``JIBOT_CONNECTION_LOST``.
    Use that state error to make the operator-facing ONLINE/OFFLINE label match
    the real AMR instead of the local process.
    """

    from core.monitor import FileMonitor  # pyright: ignore[reportMissingImports]

    class SeerFileMonitor(FileMonitor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._seer_vehicle_online = False

        @staticmethod
        def _vehicle_link_lost(snapshot) -> bool:
            for error in getattr(snapshot, "errors", ()) or ():
                if not isinstance(error, dict):
                    continue
                error_type = str(
                    error.get("errorType") or error.get("error_type") or ""
                ).upper()
                if error_type in {
                    "JIBOT_CONNECTION_LOST",
                    "SEER_CONNECTION_LOST",
                    "VEHICLE_LINK_LOST",
                }:
                    return True
            return False

        @property
        def adapter_online(self) -> bool:
            # WebUi historically uses this property for the robot ONLINE pill.
            # Under SEER it must represent the controller link, not state-file
            # freshness. ACS/MQTT reachability has its own acs_broker_connected.
            return bool(self._seer_vehicle_online)

        @property
        def broker_connected(self) -> bool:
            # Back-compat surface read by the unchanged web/server.py after it
            # calls get_snapshot().  Return the same vehicle-link result.
            return bool(self._seer_vehicle_online)

        def get_snapshot(self):
            snapshot = super().get_snapshot()
            adapter_alive = bool(getattr(self, "_fresh", False))
            self._seer_vehicle_online = bool(
                adapter_alive and not self._vehicle_link_lost(snapshot)
            )
            snapshot.connection_state = (
                "ONLINE" if self._seer_vehicle_online else "OFFLINE"
            )
            if snapshot.paused is None:
                snapshot.paused = str(snapshot.working_state or "").upper() == "PAUSED"
            snapshot.blocked = bool(
                str(snapshot.working_state or "").upper() == "BLOCKED"
                or snapshot.field_violation
            )
            return snapshot

    return SeerFileMonitor(serial)


def _toml_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _replace_scalar(text: str, section: str, key: str, literal: str) -> str:
    """Replace or add one scalar in a TOML section without reformatting it."""

    lines = text.splitlines(keepends=True)
    active = ""
    section_found = False
    insert_at: Optional[int] = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            next_section = stripped.strip("[]").strip()
            if active == section and next_section != section and insert_at is not None:
                lines.insert(insert_at, f"{key} = {literal}\n")
                return "".join(lines)
            active = next_section
            if active == section:
                section_found = True
                insert_at = index + 1
            continue
        if active != section or stripped.startswith("#") or "=" not in line:
            continue
        insert_at = index + 1
        left, _separator, _right = line.partition("=")
        if left.strip() != key:
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        indent = left[: len(left) - len(left.lstrip())]
        lines[index] = f"{indent}{key} = {literal}{newline}"
        return "".join(lines)
    if section_found:
        lines.insert(insert_at if insert_at is not None else len(lines), f"{key} = {literal}\n")
        return "".join(lines)
    raise KeyError(f"missing TOML section [{section}]")


@dataclass(frozen=True)
class DropInPaths:
    """All mutable files used by the drop-in runner.

    The source repository is read-only from this class's perspective.  A copy
    of the original config is created below ``seer_client/runtime`` and local
    IPC is also kept below ``seer_client/runtime``.  The SEER drop-in control
    transport uses localhost TCP, so it does not depend on Unix socket length.
    """

    runtime_dir: Path
    config_path: Path
    robots_path: Path
    log_path: Path
    ipc_root: Path
    # Extensions can remain shared across a fleet, but Recipes are isolated per
    # AMR so selecting/running one robot can never mutate another robot's
    # Block Builder program set. Single-AMR keeps both under runtime_dir.
    hcl_dir: Optional[Path] = None
    recipes_hcl_dir: Optional[Path] = None

    @property
    def extensions_path(self) -> Path:
        from .hcl_config import paths_for

        return paths_for(self.hcl_dir or self.runtime_dir).extensions

    @property
    def recipes_path(self) -> Path:
        from .hcl_config import paths_for

        return paths_for(
            self.recipes_hcl_dir or self.hcl_dir or self.runtime_dir
        ).recipes

    @classmethod
    def defaults(cls) -> "DropInPaths":
        runtime = SEER_CLIENT_ROOT / "runtime"
        return cls(
            runtime_dir=runtime,
            config_path=runtime / "config.toml",
            robots_path=runtime / "robots.toml",
            log_path=runtime / "seer-adapter.log",
            ipc_root=runtime / "ipc",
            hcl_dir=runtime,
            recipes_hcl_dir=runtime,
        )

    @classmethod
    def for_robot(
        cls,
        serial: str,
        *,
        runtime_root: Optional[Path] = None,
        ipc_root: Optional[Path] = None,
    ) -> "DropInPaths":
        """Return isolated config/log paths for one member of a fleet."""

        from core.ipc_paths import safe_serial  # pyright: ignore[reportMissingImports]

        base = Path(runtime_root) if runtime_root is not None else SEER_CLIENT_ROOT / "runtime"
        robot_runtime = base / safe_serial(serial)
        return cls(
            runtime_dir=robot_runtime,
            config_path=robot_runtime / "config.toml",
            robots_path=base / "robots.toml",
            log_path=robot_runtime / "seer-adapter.log",
            ipc_root=(
                Path(ipc_root)
                if ipc_root is not None
                else base / "ipc"
            ),
            # Facility extensions stay fleet-shared; recipes are member-local.
            hcl_dir=base,
            recipes_hcl_dir=robot_runtime,
        )

    def prepare(
        self,
        *,
        serial: str,
        vehicle_ip: str,
        simulator: bool,
        web_host: str,
        web_port: int,
        state_port: int = 19204,
        mqtt_host: Optional[str] = None,
        mqtt_port: Optional[int] = None,
    ) -> None:
        from .hcl_config import ensure_hcl_files

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.ipc_root.mkdir(parents=True, exist_ok=True)
        shared_hcl_dir = self.hcl_dir or self.runtime_dir
        shared_paths = ensure_hcl_files(shared_hcl_dir)
        recipe_dir = self.recipes_hcl_dir or shared_hcl_dir
        if Path(recipe_dir) != Path(shared_hcl_dir):
            # Upgrade path from the old fleet-shared Recipe file: copy the
            # current shared contents once, then each AMR owns its copy.
            from .hcl_config import paths_for

            recipe_target = paths_for(Path(recipe_dir)).recipes
            if not recipe_target.exists():
                recipe_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shared_paths.recipes, recipe_target)
        if not self.config_path.exists():
            shutil.copy2(ADAPTOR_ROOT / "config" / "config.toml", self.config_path)

        text = self.config_path.read_text(encoding="utf-8")
        updates = (
            ("vehicle", "manufacturer", _toml_string("seer")),
            ("vehicle", "serial_number", _toml_string(serial)),
            ("vehicle", "vehicle_ip", _toml_string(vehicle_ip or "127.0.0.1")),
            ("vehicle", "vehicle_port", str(int(state_port))),
            ("factsheet", "series_name", _toml_string("SEER")),
            # The original JIBOT config publishes state every 5 seconds. SEER
            # Live state supports a real 1-second WebUI refresh, so keep the
            # generated drop-in config at the same source cadence.
            ("settings", "state_publish_delay", "1.0"),
            ("web_ui", "enabled", "true"),
            ("web_ui", "host", _toml_string(web_host)),
            ("web_ui", "port", str(int(web_port))),
            # Browser polling is independent from state_publish_delay, but a
            # SEER dashboard should show the new 1 Hz state by default.
            ("web_ui", "page_default_refresh_sec", "1"),
        )
        for section, key, literal in updates:
            text = _replace_scalar(text, section, key, literal)
        if mqtt_host is not None:
            text = _replace_scalar(
                text, "mqtt_broker", "host", _toml_string(mqtt_host)
            )
        if mqtt_port is not None:
            text = _replace_scalar(text, "mqtt_broker", "port", str(int(mqtt_port)))
        self.config_path.write_text(text, encoding="utf-8")

        robot_lines = [
            "# Managed by seer_client/run_webui.py; the original robots.toml is untouched.",
            "[[robot]]",
            f"id = {_toml_string(serial)}",
            f"vehicle_ip = {_toml_string(vehicle_ip or '127.0.0.1')}",
            f"simulator = {str(bool(simulator)).lower()}",
        ]
        self.robots_path.write_text("\n".join(robot_lines) + "\n", encoding="utf-8")


class SeerProcessController:
    """Controller API expected by the original WebUI, backed by ``Popen``."""

    def __init__(
        self,
        command_factory: Callable[[], Sequence[str]],
        *,
        workdir: Path,
        log_path: Path,
        ipc_root: Path,
        control_host: str,
        control_port: int,
        map_cache_path: Optional[Path] = None,
        io_cache_path: Optional[Path] = None,
        active_route_path: Optional[Path] = None,
        stop_timeout: float = 8.0,
    ) -> None:
        self._command_factory = command_factory
        self._workdir = Path(workdir)
        self._log_path = Path(log_path)
        self._ipc_root = Path(ipc_root)
        self._map_cache_path = Path(map_cache_path) if map_cache_path else None
        self._io_cache_path = Path(io_cache_path) if io_cache_path else None
        self._active_route_path = (
            Path(active_route_path) if active_route_path else None
        )
        self._control_host = str(control_host)
        self._control_port = int(control_port)
        self._stop_timeout = float(stop_timeout)
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._log_handle = None
        self._started_at: Optional[float] = None
        self._last_returncode: Optional[int] = None
        self._launch_count = 0

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _refresh(self) -> Optional[subprocess.Popen]:
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_returncode = process.returncode
            self._process = None
            self._started_at = None
            self._close_log()
            if self._active_route_path is not None:
                clear_active_route(self._active_route_path)
                try:
                    self._active_route_path.with_name("seer-block-runtime.json").unlink()
                except FileNotFoundError:
                    pass
            return None
        return process

    def start(self) -> Tuple[bool, str]:
        with self._lock:
            process = self._refresh()
            if process is not None:
                return True, f"SEER adapter already running (pid={process.pid})"
            command = [str(part) for part in self._command_factory()]
            if self._active_route_path is not None:
                clear_active_route(self._active_route_path)
                block_runtime_path = self._active_route_path.with_name("seer-block-runtime.json")
                try:
                    block_runtime_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                block_runtime_path = None
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self._log_path.open(
                "a", encoding="utf-8", buffering=1
            )
            self._log_handle.write(
                f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] launch: "
                + " ".join(command)
                + "\n"
            )
            env = os.environ.copy()
            env["SEER_RUNTIME_ROOT"] = str(self._ipc_root)
            env["SEER_CONTROL_IPC_HOST"] = self._control_host
            env["SEER_CONTROL_IPC_PORT"] = str(self._control_port)
            if self._map_cache_path is not None:
                # Runtime directories are isolated per AMR/IP.  Keep that
                # member's last good cache visible while a fresh CONFIG 4011
                # transfer is in progress; successful downloads replace it
                # atomically.  This avoids a blank map after a transient link
                # failure without ever borrowing another AMR's cache.
                env["SEER_MAP_CACHE_PATH"] = str(self._map_cache_path)
                status_cache_path = self._map_cache_path.with_name("seer-controller-status.json")
                try:
                    status_cache_path.unlink()
                except FileNotFoundError:
                    pass
                env["SEER_STATUS_CACHE_PATH"] = str(status_cache_path)
                jack_status_path = self._map_cache_path.with_name("seer-jack-status.json")
                try:
                    jack_status_path.unlink()
                except FileNotFoundError:
                    pass
                env["SEER_JACK_STATUS_CACHE_PATH"] = str(jack_status_path)
                docking_status_path = self._map_cache_path.with_name("seer-docking-status.json")
                docking_preview_path = self._map_cache_path.with_name("seer-docking-preview.jpg")
                for docking_path in (docking_status_path, docking_preview_path):
                    try:
                        docking_path.unlink()
                    except FileNotFoundError:
                        pass
                env["SEER_DOCKING_STATUS_PATH"] = str(docking_status_path)
                env["SEER_DOCKING_PREVIEW_PATH"] = str(docking_preview_path)
                env["SEER_DOCKING_CONFIG_PATH"] = str(SEER_CLIENT_ROOT / "config" / "docking.json")
            if self._io_cache_path is not None:
                env["SEER_IO_CACHE_PATH"] = str(self._io_cache_path)
            if self._active_route_path is not None:
                env["SEER_ACTIVE_ROUTE_PATH"] = str(self._active_route_path)
            if block_runtime_path is not None:
                env["SEER_BLOCK_RUNTIME_PATH"] = str(block_runtime_path)
            # Importing the unchanged Adapter must not create __pycache__ files
            # in its source tree. All intentional output is below SEER runtime.
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env.setdefault("PYTHONUTF8", "1")
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=str(self._workdir),
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
            except Exception as exc:
                self._close_log()
                return False, f"SEER adapter start failed: {exc}"
            self._started_at = time.monotonic()
            self._last_returncode = None
            self._launch_count += 1
            return True, f"SEER adapter started (pid={self._process.pid})"

    def stop(self) -> Tuple[bool, str]:
        with self._lock:
            process = self._refresh()
            if process is None:
                return True, "SEER adapter already stopped"
            process.terminate()
            try:
                process.wait(timeout=self._stop_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            actual_returncode = process.returncode
            # Windows TerminateProcess commonly reports a non-zero exit code.
            # This path is an operator-requested Stop, so the dashboard must
            # show inactive/dead rather than failed.
            self._last_returncode = 0
            self._process = None
            self._started_at = None
            self._close_log()
            if self._active_route_path is not None:
                clear_active_route(self._active_route_path)
                try:
                    self._active_route_path.with_name("seer-block-runtime.json").unlink()
                except FileNotFoundError:
                    pass
            return True, f"SEER adapter stopped (exit={actual_returncode})"

    def restart(self) -> Tuple[bool, str]:
        stopped, message = self.stop()
        if not stopped:
            return False, message
        return self.start()

    def enable(self) -> Tuple[bool, str]:
        return False, "standalone SEER runner has no systemd enable operation"

    def disable(self) -> Tuple[bool, str]:
        return False, "standalone SEER runner has no systemd disable operation"

    def poll(self):
        from core.systemd import ServiceMetrics  # pyright: ignore[reportMissingImports]

        with self._lock:
            process = self._refresh()
            if process is not None:
                uptime = (
                    max(0.0, time.monotonic() - self._started_at)
                    if self._started_at is not None
                    else None
                )
                return ServiceMetrics(
                    exists=True,
                    load_state="loaded",
                    active_state="active",
                    sub_state="running",
                    enabled_state="static",
                    main_pid=process.pid,
                    restarts=max(0, self._launch_count - 1),
                    uptime_sec=uptime,
                )
            failed = self._last_returncode not in (None, 0)
            return ServiceMetrics(
                exists=True,
                load_state="loaded",
                active_state="failed" if failed else "inactive",
                sub_state="failed" if failed else "dead",
                enabled_state="static",
                restarts=max(0, self._launch_count - 1),
            )

    def journal_argv(self, lines: int = 300, follow: bool = True):
        del follow
        return [
            sys.executable,
            str(PRINT_LOG),
            str(self._log_path),
            "--lines",
            str(int(lines)),
        ]

    def manual_command(self, verb: str) -> str:
        return f"python {SEER_CLIENT_ROOT / 'run_webui.py'}  # {verb} from WebUI"


def _decorate_seer_adapter_navigation(render, output: str, spec, *, current: str) -> str:
    """Arrange the SEER detail navigation and title as two clean rows.

    The original detail renderer also puts lowercase actions/tests/logs in the
    ``head-actions`` area.  ``Actions`` already exists in the global navigation,
    so keeping that second copy wastes enough width to wrap the controls onto a
    new row.  SEER keeps the global Actions entry and exposes only the two
    robot-scoped destinations beside Factsheet.
    """

    if getattr(spec, "manufacturer", "") != "seer":
        return output

    key = render.esc(getattr(spec, "key", ""))
    # Remove only the duplicate detail-header links. The global /actions entry
    # is intentionally retained.
    for leaf in ("actions", "tests", "logs"):
        output = output.replace(
            f'<a class="btn ghost" href="/adapter/{key}/{leaf}">{leaf}</a>',
            "",
            1,
        )

    # Keep the robot title and Refresh control together, but place that group
    # on its own full-width row below the global navigation.  The class is
    # SEER-specific so the original Adapter renderer and other manufacturers
    # remain untouched.
    output = output.replace(
        '<div class="head-actions">',
        '<div class="head-actions seer-detail-heading">',
        1,
    )

    nav_end = output.find("</nav>")
    if nav_end < 0:
        return output
    tests_href = f'/adapter/{key}/tests'
    logs_href = f'/adapter/{key}/logs'
    # The decorator is shared by dashboard/actions/tests/logs renderers. Avoid
    # adding the context links twice if another wrapper has already handled it.
    navigation = output[:nav_end]
    if tests_href in navigation or logs_href in navigation:
        return output
    tests_current = ' aria-current="page"' if current == "tests" else ""
    logs_current = ' aria-current="page"' if current == "logs" else ""
    links = (
        f'<a class="nav-link seer-context-link" href="{tests_href}"'
        f'{tests_current}>Tests</a>'
        f'<a class="nav-link seer-context-link" href="{logs_href}"'
        f'{logs_current}>Logs</a>'
    )
    return output[:nav_end] + links + output[nav_end:]


def _bind_seer_hcl_sources(web, paths: DropInPaths) -> None:
    """Point the unchanged source editor at SEER-local persistent HCL files."""

    def hcl_paths(self):
        return {
            "extensions.hcl": paths.extensions_path,
            "recipes.hcl": paths.recipes_path,
            "robots.hcl": self._robots_path,
        }

    web._hcl_paths = MethodType(hcl_paths, web)
    web._seer_recipes_path = paths.recipes_path


def _bind_seer_recipe_builder(
    web,
    *,
    validate_recipe: Callable[[Path], Tuple[bool, str]],
    apply_recipe: Callable[[], Tuple[bool, str]],
) -> None:
    """Attach SEER-only block Recipe validation/apply callbacks to WebUi."""

    web._seer_validate_recipe = validate_recipe
    web._seer_apply_recipe = apply_recipe


def _suppress_client_disconnect_tracebacks(web) -> None:
    """Silence expected browser disconnects without hiding server failures.

    Chrome/Edge can cancel an in-flight auto-refresh GET when a newer refresh,
    navigation, or tab close wins.  Windows reports that normal client-side
    cancellation as WinError 10053/10054 while ``wfile.write`` is sending the
    HTML body.  ``socketserver`` otherwise prints a full traceback even though
    the WebUI and Adapter remain healthy.
    """

    server = getattr(web, "_server", None)
    if server is None or bool(getattr(server, "_seer_disconnect_filter", False)):
        return
    original_handle_error = server.handle_error

    def handle_error(self, request, client_address):
        del self
        error = sys.exception()
        if isinstance(
            error,
            (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
        ):
            return
        return original_handle_error(request, client_address)

    server.handle_error = MethodType(handle_error, server)
    server._seer_disconnect_filter = True


def _require_motion_confirmation(specs: Sequence[object]) -> None:
    """Apply the WebUI confirmation gate to every configured motion Recipe."""

    from web import server  # pyright: ignore[reportMissingImports]

    required = list(server._CONFIRM_REQUIRED_ACTIONS)
    for spec in specs:
        for recipe in getattr(spec, "recipes", ()):
            action_type = str(getattr(recipe, "action_type", ""))
            if bool(getattr(recipe, "motion", False)) and action_type not in required:
                required.append(action_type)
    server._CONFIRM_REQUIRED_ACTIONS = tuple(required)


class SeerWebUiApplication:
    """Build and serve the original WebUI for one SEER real/simulated AMR."""

    def __init__(
        self,
        *,
        serial: str,
        simulator: bool,
        vehicle_ip: str,
        username: str,
        password: str,
        host: str = "127.0.0.1",
        port: int = 9010,
        mqtt_host: Optional[str] = None,
        mqtt_port: Optional[int] = None,
        x: float = 0.0,
        y: float = 0.0,
        theta: float = 0.0,
        battery: float = 70.0,
        charging: bool = False,
        state_port: int = 19204,
        control_port: int = 19205,
        task_port: int = 19206,
        config_port: int = 19207,
        other_port: int = 19210,
        motor_names: str = "",
        auto_start: bool = True,
        webui_vda_transport: str = "mqtt",
        paths: Optional[DropInPaths] = None,
        control_ipc_port: Optional[int] = None,
    ) -> None:
        if not serial.strip():
            raise ValueError("serial is required")
        if not simulator and not vehicle_ip.strip():
            raise ValueError("--vehicle-ip is required without --simulator")
        if not username.strip():
            raise ValueError("WebUI username is required")
        if len(password) < 12:
            raise ValueError("WebUI password must be at least 12 characters")
        if any(
            token in password.lower()
            for token in ("set-me", "changeme", "password", "admin")
        ):
            raise ValueError("WebUI password contains a forbidden placeholder token")

        self.serial = serial.strip()
        self.simulator = bool(simulator)
        self.vehicle_ip = vehicle_ip.strip()
        self.username = username.strip()
        self.password = password
        self.host = host
        self.requested_port = int(port)
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.x, self.y, self.theta = float(x), float(y), float(theta)
        self.battery = max(0.0, min(100.0, float(battery)))
        self.charging = bool(charging)
        self.state_port = int(state_port)
        self.control_port = int(control_port)
        self.task_port = int(task_port)
        self.config_port = int(config_port)
        self.other_port = int(other_port)
        self.motor_names = motor_names
        self.auto_start = bool(auto_start)
        self.webui_vda_transport = str(webui_vda_transport or "mqtt").strip().lower()
        if self.webui_vda_transport not in {"local", "mqtt"}:
            raise ValueError("webui_vda_transport must be 'local' or 'mqtt'")
        self.paths = paths or DropInPaths.defaults()
        from .dropin_control import reserve_local_port

        self.control_host = "127.0.0.1"
        # Keep the SEER vehicle CONTROL port separate from the localhost-only
        # WebUI -> Adapter command channel. Fleet callers can pre-allocate a
        # distinct port for every member; single-AMR keeps the proven original
        # behavior and asks the OS for one free port here.
        self.control_ipc_port = (
            int(control_ipc_port)
            if control_ipc_port is not None
            else reserve_local_port()
        )
        self.paths.prepare(
            serial=self.serial,
            vehicle_ip=self.vehicle_ip,
            simulator=self.simulator,
            web_host=self.host,
            web_port=self.requested_port,
            state_port=self.state_port,
            mqtt_host=self.mqtt_host,
            mqtt_port=self.mqtt_port,
        )
        self.controller = SeerProcessController(
            self.adapter_command,
            workdir=self.paths.runtime_dir,
            log_path=self.paths.log_path,
            ipc_root=self.paths.ipc_root,
            map_cache_path=self.paths.runtime_dir / "seer-map.json",
            io_cache_path=self.paths.runtime_dir / "seer-io.json",
            active_route_path=self.paths.runtime_dir / "seer-active-route.json",
            control_host=self.control_host,
            control_port=self.control_ipc_port,
        )
        self.web = None
        self.vda_traces = {}

    def adapter_command(self) -> Sequence[str]:
        command = [
            sys.executable,
            str(RUN_ADAPTER),
            "--config",
            str(self.paths.config_path),
            "--seer-extensions-path",
            str(self.paths.extensions_path),
            "--seer-recipes-path",
            str(self.paths.recipes_path),
            "--id",
            self.serial,
        ]
        if self.simulator:
            command.extend(
                (
                    "--simulator",
                    "--x",
                    str(self.x),
                    "--y",
                    str(self.y),
                    "--theta",
                    str(self.theta),
                    "--battery",
                    str(self.battery),
                )
            )
            if self.charging:
                command.append("--charging")
        else:
            command.extend(("--vehicle-ip", self.vehicle_ip))
        if self.mqtt_host:
            command.extend(("--mqtt-host", self.mqtt_host))
        if self.mqtt_port:
            command.extend(("--mqtt-port", str(self.mqtt_port)))
        command.extend(
            (
                "--seer-state-port",
                str(self.state_port),
                "--seer-control-port",
                str(self.control_port),
                "--seer-task-port",
                str(self.task_port),
                "--seer-config-port",
                str(self.config_port),
                "--seer-other-port",
                str(self.other_port),
            )
        )
        if self.motor_names:
            command.extend(("--seer-motor-names", self.motor_names))
        return command

    def _spec(
        self,
        config,
        *,
        key: str = "seer",
        display_name: str = "SEER Adapter",
    ):
        from config.recipes import recipe_variables  # pyright: ignore[reportMissingImports]
        from core.action_registry import ActionParameterSpec  # pyright: ignore[reportMissingImports]
        from core.registry import (  # pyright: ignore[reportMissingImports]
            ActionModuleView,
            AdaptorSpec,
            Diagnostic,
            InstantAction,
            RecipeView,
        )

        configured_enabled = {
            str(getattr(action, "action_type", "")): bool(
                getattr(action, "enabled", True)
            )
            for action in getattr(config, "actions", ())
        }
        path_nav = InstantAction(
            "seerPathNav",
            "SEER Path Navigation",
            motion=True,
            parameters=(
                ActionParameterSpec("id", required=True, placeholder="LM1"),
                ActionParameterSpec(
                    "navigation_mode",
                    required=True,
                    choices=("path", "free", "reentry"),
                ),
                ActionParameterSpec("source_id", placeholder="SELF_POSITION"),
                ActionParameterSpec("task_id", placeholder="optional task id"),
                ActionParameterSpec(
                    "route_points",
                    placeholder='["LM1", "LM5", "LM4"]',
                    value_type="json",
                ),
                ActionParameterSpec("free_nav_x", input_type="number"),
                ActionParameterSpec("free_nav_y", input_type="number"),
                ActionParameterSpec("free_nav_theta", input_type="number"),
                ActionParameterSpec("reentry_id", placeholder="LM1"),
                ActionParameterSpec("reentry_x", input_type="number"),
                ActionParameterSpec("reentry_y", input_type="number"),
                ActionParameterSpec("reentry_theta", input_type="number"),
                ActionParameterSpec(
                    "waypoint_delay_sec",
                    input_type="number",
                    placeholder="0",
                ),
            ),
        )
        coordinate_nav = InstantAction(
            "seerCoordinateNav",
            "SEER Coordinate Navigation",
            motion=True,
            parameters=(
                ActionParameterSpec("x", required=True, input_type="number", placeholder="0.0 m"),
                ActionParameterSpec("y", required=True, input_type="number", placeholder="0.0 m"),
                ActionParameterSpec("theta_deg", required=True, input_type="number", placeholder="0 deg"),
            ),
        )
        translate = InstantAction(
            "seerTranslate",
            "SEER Straight Translation",
            motion=True,
            parameters=(
                ActionParameterSpec("distance_m", required=True, input_type="number", placeholder="0.1"),
                ActionParameterSpec("linear_speed_mps", required=True, input_type="number", placeholder="0.05"),
                ActionParameterSpec("lateral", choices=("forward", "lateral")),
            ),
        )
        turn = InstantAction(
            "seerTurn",
            "SEER Turn",
            motion=True,
            parameters=(
                ActionParameterSpec("angle_deg", required=True, input_type="number", placeholder="90"),
                ActionParameterSpec("angular_speed_deg_s", required=True, input_type="number", placeholder="5"),
            ),
        )
        set_do = InstantAction(
            "seerSetDO",
            "SEER Digital Output",
            motion=False,
            parameters=(
                ActionParameterSpec("id", required=True, input_type="number"),
                ActionParameterSpec(
                    "status", required=True, choices=("on", "off")
                ),
            ),
        )
        jack_load = InstantAction(
            "seerJackLoad",
            "SEER - Jack 올리기",
            motion=True,
        )
        jack_unload = InstantAction(
            "seerJackUnload",
            "SEER - Jack 내리기",
            motion=True,
        )
        wait_action = InstantAction(
            "seerWait",
            "SEER Recipe Wait",
            motion=False,
            parameters=(
                ActionParameterSpec("seconds", input_type="number", placeholder="1"),
            ),
        )
        camera_dock_preview = InstantAction(
            "seerCameraDockPreview",
            "SEER Camera Docking Preview",
            motion=False,
            parameters=(
                ActionParameterSpec("max_runtime_s", input_type="number", placeholder="300"),
            ),
        )
        camera_dock = InstantAction(
            "seerCameraDock",
            "SEER Camera Docking Start",
            motion=True,
            parameters=(
                ActionParameterSpec("max_runtime_s", input_type="number", placeholder="300"),
            ),
        )
        seer_extension_actions = tuple(
            action
            for action in (
                path_nav,
                coordinate_nav,
                translate,
                turn,
                set_do,
                jack_load,
                jack_unload,
                wait_action,
                camera_dock_preview,
                camera_dock,
            )
            if configured_enabled.get(action.action_type, True)
        )
        # Jack controls are intentionally *not* placed in the SEER TCP/IP
        # extension module.  They are operator-facing vehicle actions and should
        # render beside Request state / Pause / Resume on the dashboard.
        # The actions still remain in ``spec.instant_actions`` and therefore use
        # the exact same VDA5050 instantActions dispatch path.
        module_actions = tuple(
            action
            for action in seer_extension_actions
            if action.action_type not in {
                "seerJackLoad", "seerJackUnload",
                "seerCameraDockPreview", "seerCameraDock",
            }
        )
        camera_actions = tuple(
            action
            for action in (camera_dock_preview, camera_dock)
            if configured_enabled.get(action.action_type, True)
        )
        jack_vehicle_actions = tuple(
            action
            for action in (jack_load, jack_unload)
            if configured_enabled.get(action.action_type, True)
        )
        action_modules = (
            ActionModuleView(
                module="seer_client.tcp_actions",
                title="SEER TCP/IP Actions",
                actions=module_actions,
            ),
        ) if module_actions else ()

        recipe_views = tuple(
            RecipeView(
                action_type=recipe.action_type,
                label=recipe.label or recipe.action_type,
                motion=recipe.motion,
                variables=recipe_variables(recipe),
                step_count=len(recipe.steps),
                cleanup_count=len(recipe.cleanup),
            )
            for recipe in getattr(config, "recipes", ())
            if bool(getattr(recipe, "enabled", True))
        )
        _SEER_RECIPE_ACTION_TYPES[key] = {
            view.action_type for view in recipe_views
        }
        recipe_actions = tuple(
            InstantAction(
                view.action_type,
                view.label,
                motion=view.motion,
                parameters=tuple(
                    ActionParameterSpec(
                        name,
                        required=False,
                        placeholder="비우면 블록에 저장된 기본값 사용",
                    )
                    for name in view.variables
                ),
            )
            for view in recipe_views
        )

        actions = (
            InstantAction("stateRequest", "Request state", motion=False),
            InstantAction("startPause", "Pause", motion=True),
            InstantAction("stopPause", "Resume", motion=True),
            InstantAction("factsheetRequest", "Request factsheet", motion=False),
            InstantAction("cancelOrder", "Order 취소", motion=True),
            InstantAction("clearErrors", "VDA 오류 리셋", motion=False),
            InstantAction("seerCancelActiveAction", "Action 취소", motion=False),
            InstantAction("seerResetActionErrors", "Action 오류 리셋", motion=False),
            InstantAction("clearInstantActions", "Action 기록 지우기", motion=False),
            InstantAction("testSound", "Sound test", motion=False),
            InstantAction("stopSound", "Stop sound", motion=False),
            InstantAction("manualDrive", "Manual drive", motion=True),
            InstantAction("manualMove", "Manual move", motion=True),
            InstantAction("manualStop", "Manual stop", motion=False),
            *module_actions,
            *camera_actions,
            InstantAction(
                "seerEmergencySwitch",
                "SEER software emergency switch",
                motion=True,
            ),
            InstantAction("enableMotor", "Enable motor", motion=True),
            InstantAction("disableMotor", "Disable motor", motion=True),
            *recipe_actions,
            # Keep the existing Vehicle actions layout (factsheet + the two
            # recipe shortcuts on row two) and append Jack controls after it.
            *jack_vehicle_actions,
        )
        common = (
            "--config",
            str(self.paths.config_path),
            "--seer-extensions-path",
            str(self.paths.extensions_path),
            "--seer-recipes-path",
            str(self.paths.recipes_path),
            "--id",
            self.serial,
        )
        simulator_smoke = (
            "{py}",
            str(RUN_ADAPTER),
            *common,
            "--simulator",
            "--vehicle-smoke-test",
        )
        real_smoke = ["{py}", str(RUN_ADAPTER), *common]
        if self.vehicle_ip:
            real_smoke.extend(("--vehicle-ip", self.vehicle_ip))
        real_smoke.append("--vehicle-smoke-test")
        prefix = (
            f"{config.mqtt_broker.vda_interface}/"
            f"{config.vehicle.vda_version}/{self.serial}"
        )
        return AdaptorSpec(
            key=key,
            display_name=display_name,
            unit=f"seer-dropin:{self.serial}",
            workdir=self.paths.runtime_dir,
            exec_script=str(RUN_ADAPTER),
            manufacturer="seer",
            serial=self.serial,
            vda_full_version=config.vehicle.vda_full_version,
            topic_prefix=prefix,
            mqtt_host=config.mqtt_broker.host,
            mqtt_port=config.mqtt_broker.port,
            vehicle_host=self.vehicle_ip or "127.0.0.1",
            vehicle_port=self.state_port,
            test_suites=(
                Diagnostic(
                    "unittest: SEER client",
                    (
                        "{py}", "-m", "unittest", "discover", "-s",
                        str(SEER_CLIENT_ROOT / "tests"), "-p", "test_*.py", "-v",
                    ),
                ),
                Diagnostic(
                    "unittest: SEER simulator",
                    (
                        "{py}", "-m", "unittest", "discover", "-s",
                        str(REPO_ROOT / "seer_simulator" / "tests"),
                        "-p", "test_*.py", "-v",
                    ),
                ),
            ),
            diagnostics=(
                Diagnostic(
                    "smoke test (SEER simulator)",
                    simulator_smoke,
                    "Starts five local SEER TCP ports and reads one state snapshot.",
                ),
                Diagnostic(
                    "smoke test (real SEER)",
                    tuple(real_smoke),
                    "Read-only connection and state snapshot from the configured controller.",
                    requires_manual=True,
                ),
            ),
            instant_actions=actions,
            action_modules=action_modules,
            recipes=recipe_views,
        )

    @staticmethod
    def _patch_seer_title() -> None:
        from web import render  # pyright: ignore[reportMissingImports]

        if getattr(render, "_seer_dropin_title_installed", False):
            return
        original = render._model_header_title

        def title(spec):
            if getattr(spec, "manufacturer", "") == "seer":
                return f"SEER Adaptor - {_seer_nickname_ip_label(spec)}"
            return original(spec)

        render._model_header_title = title
        render._seer_dropin_title_installed = True

    @staticmethod
    def _patch_seer_operator_ui() -> None:
        """Extend the imported original renderer without changing it on disk."""

        from web import render  # pyright: ignore[reportMissingImports]
        from web import server  # pyright: ignore[reportMissingImports]

        if getattr(render, "_seer_dropin_operator_ui_installed", False):
            return
        original_live = render._mqtt_live_table
        original_instant_action_cards = render._instant_action_cards
        original_jog = render._jog_card
        original_emergency = render._emergency_forms
        original_poll_script = render._poll_script
        original_nav = render._nav
        original_page = render.page
        original_refresh_secs = render._refresh_secs
        original_run_form = render._run_form
        original_card_action_form = render._card_action_form
        original_detail_page = render.adapter_detail_page
        original_actions_page = render.actions_page
        original_tests_page = render.tests_page
        original_logs_page = render.logs_page
        original_adapter_list_page = render.adapter_list_page
        original_dispatch_get = server.WebUi._dispatch_get
        original_dispatch_post = server.WebUi._dispatch_post

        def adapter_list_page(
            rows, camera_metrics=None, csrf: str = "", q: dict | None = None,
            startup_error: str = "",
        ):
            rows = list(rows)
            seer_rows = [
                row for row in rows
                if getattr(row[0], "manufacturer", "") == "seer"
            ]
            if not seer_rows:
                return original_adapter_list_page(
                    rows,
                    camera_metrics=camera_metrics,
                    csrf=csrf,
                    q=q,
                    startup_error=startup_error,
                )

            body_rows = []
            for spec, metrics, snap in seer_rows:
                ip = str(getattr(spec, "vehicle_host", "") or "-")
                connection = (
                    str(getattr(snap, "connection_state", "") or "-")
                    if snap is not None else "-"
                )
                mode = (
                    str(
                        getattr(snap, "working_state", "")
                        or getattr(snap, "operating_mode", "")
                        or "-"
                    )
                    if snap is not None else "-"
                )
                battery = getattr(snap, "battery_soc", None) if snap is not None else None
                battery_text = "-" if battery is None else f"{float(battery):.0f}%"
                charging = getattr(snap, "charging", None) if snap is not None else None
                charging_text = (
                    "충전 중" if charging is True
                    else "충전 아님" if charging is False
                    else "확인 불가"
                )
                point = (
                    str(getattr(snap, "last_node_id", "") or "-")
                    if snap is not None else "-"
                )
                view = _SEER_MAP_VIEWS.get(getattr(spec, "key", ""), {})
                cache_path = view.get("cache_path")
                map_text = "없음"
                if cache_path is not None:
                    try:
                        cache_file = Path(cache_path)
                        if cache_file.is_file():
                            age = max(0.0, time.time() - cache_file.stat().st_mtime)
                            model = read_map_cache(cache_file)
                            map_name = str(
                                model.get("map_name", "") if isinstance(model, dict) else ""
                            ).strip() or "map"
                            map_text = f"{map_name} · {age:.0f}s 전"
                    except (OSError, ValueError, TypeError):
                        map_text = "확인 불가"
                service = str(getattr(metrics, "active_state", "") or "-")
                key = render.esc(spec.key)
                display_raw = str(
                    getattr(spec, "display_name", "")
                    or getattr(spec, "serial", "")
                    or ip
                )
                display = render.esc(display_raw)
                nickname_form = (
                    f'<form method="post" action="/seer/nickname" '
                    'style="display:flex;gap:6px;align-items:center;margin:0">'
                    f'<input type="hidden" name="csrf_token" value="{render.esc(csrf)}">'
                    f'<input type="hidden" name="key" value="{key}">'
                    f'<input name="nickname" value="{display}" maxlength="40" '
                    'style="width:150px" aria-label="AMR nickname">'
                    '<button class="btn ghost" type="submit">저장</button></form>'
                )
                refresh_form = (
                    '<form method="post" action="/seer/map-refresh" style="margin:0">'
                    f'<input type="hidden" name="csrf_token" value="{render.esc(csrf)}">'
                    f'<input type="hidden" name="key" value="{key}">'
                    '<button class="btn ghost" type="submit">맵 새로고침</button></form>'
                )
                conn_cls = "success" if connection.upper() == "ONLINE" else "danger"
                body_rows.append(
                    '<tr>'
                    f'<td><strong>{display}</strong><div style="margin-top:6px">'
                    f'{nickname_form}</div></td>'
                    f'<td><code>{render.esc(ip)}</code></td>'
                    f'<td>{render._pill(connection, conn_cls)}</td>'
                    f'<td>{render.esc(service)}</td>'
                    f'<td>{render.esc(mode)}</td>'
                    f'<td>{render.esc(battery_text)}</td>'
                    f'<td>{render.esc(charging_text)}</td>'
                    f'<td>{render.esc(point)}</td>'
                    f'<td>{render.esc(map_text)}</td>'
                    f'<td><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'
                    f'<a class="btn" href="/adapter/{key}">선택</a>{refresh_form}</div></td>'
                    '</tr>'
                )

            table = (
                '<div class="section"><h2>SEER AMR 목록</h2>'
                '<p class="muted">AMR은 IP로 구분됩니다. 선택한 AMR 상세 화면에서 '
                'Path Nav, 수동조작, Vehicle actions, Jack, Block Builder를 실행합니다.</p>'
                '<div style="overflow-x:auto"><table>'
                '<tr><th>닉네임</th><th>IP</th><th>연결</th><th>Adapter</th>'
                '<th>상태</th><th>배터리</th><th>충전</th><th>현재 포인트</th><th>맵</th><th>제어</th></tr>'
                + ''.join(body_rows)
                + '</table></div></div>'
            )
            startup_notice = (
                render._error_notice("어댑터 기동 오류", startup_error)
                if startup_error else ""
            )
            body = f"{render._flash(q or {})}{startup_notice}{table}"
            return render.page(
                "adapters",
                render._with_poll(body, render._refresh_secs(q or {})),
            )

        def nav(current=""):
            output = original_nav(current)
            builder_current = ' aria-current="page"' if current == "/recipe-builder" else ""
            io_current = ' aria-current="page"' if current == "/io" else ""
            vda_current = ' aria-current="page"' if current == "/vda5050" else ""
            links = (
                f'<a class="nav-link" href="/recipe-builder"{builder_current}>Block Builder</a>'
                f'<a class="nav-link" href="/io"{io_current}>IO</a>'
                f'<a class="nav-link" href="/vda5050"{vda_current}>VDA5050</a>'
            )
            if len(_SEER_MAP_VIEWS) > 1:
                fleet_current = ' aria-current="page"' if current == "/fleet-map" else ""
                links += f'<a class="nav-link" href="/fleet-map"{fleet_current}>Fleet Map</a>'
            marker = '<a class="nav-link" href="/config"'
            index = output.find(marker)
            return output[:index] + links + output[index:] if index >= 0 else output + links

        def _docking_runtime_paths(web, key: str):
            binding = getattr(web, "_seer_member_bindings", {}).get(str(key), {})
            runtime_raw = str(binding.get("runtime_dir", "") or "").strip() if isinstance(binding, Mapping) else ""
            if not runtime_raw:
                return None, None
            runtime_dir = Path(runtime_raw)
            return runtime_dir / "seer-docking-status.json", runtime_dir / "seer-docking-preview.jpg"

        def _docking_preview_telemetry_path(preview_path):
            if preview_path is None:
                return None
            preview_path = Path(preview_path)
            return preview_path.with_name(preview_path.stem + "-telemetry.json")

        def _docking_recordings_root(web, key: str):
            status_path, _preview_path = _docking_runtime_paths(web, key)
            return None if status_path is None else Path(status_path).parent / "seer-docking-recordings"

        def _safe_docking_recording_dir(web, key: str, session_name: str):
            root = _docking_recordings_root(web, key)
            session_name = str(session_name or "").strip()
            if root is None or not session_name or session_name in {".", ".."}:
                return None
            if Path(session_name).name != session_name or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in session_name):
                return None
            candidate = root / session_name
            try:
                if not candidate.is_dir() or candidate.resolve().parent != root.resolve():
                    return None
            except OSError:
                return None
            return candidate

        def _docking_recording_entries(web, key: str, limit: int = 12):
            root = _docking_recordings_root(web, key)
            if root is None or not root.is_dir():
                return []
            entries = []
            try:
                dirs = [item for item in root.iterdir() if item.is_dir()]
                dirs.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            except OSError:
                return []
            for folder in dirs[:max(1, int(limit))]:
                meta_path = folder / "session.json"
                try:
                    meta = read_json_snapshot(meta_path, retries=2, retry_delay_s=0.001) if meta_path.is_file() else {}
                except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                video_path = folder / str(meta.get("video_file", "camera_commands.avi") or "camera_commands.avi")
                csv_path = folder / str(meta.get("telemetry_file", "telemetry.csv") or "telemetry.csv")
                entries.append({
                    "session": folder.name,
                    "meta": meta,
                    "video_exists": video_path.is_file() and video_path.stat().st_size > 0,
                    "video_size": video_path.stat().st_size if video_path.is_file() else 0,
                    "csv_exists": csv_path.is_file() and csv_path.stat().st_size > 0,
                    "csv_size": csv_path.stat().st_size if csv_path.is_file() else 0,
                })
            return entries

        def _docking_human_bytes(value):
            try:
                size = float(value or 0)
            except (TypeError, ValueError):
                return "-"
            units = ["B", "KB", "MB", "GB"]
            for unit in units:
                if size < 1024.0 or unit == units[-1]:
                    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
                size /= 1024.0
            return "-"

        def _read_camera_docking_status(web, key: str):
            status_path, preview_path = _docking_runtime_paths(web, key)
            idle = {
                "schema": 1,
                "status": "idle",
                "phase": "IDLE",
                "message": "아직 Camera Docking Action을 실행하지 않았습니다.",
                "age_sec": None,
            }
            if status_path is None:
                return idle, preview_path
            try:
                payload = read_json_snapshot(status_path, retries=3, retry_delay_s=0.001)
            except (OSError, ValueError, json.JSONDecodeError, TimeoutError):
                return idle, preview_path
            if not isinstance(payload, dict):
                return idle, preview_path
            payload = dict(payload)

            # The JPEG worker writes telemetry beside the exact frame it
            # publishes.  Merge it only when it belongs to the same camera
            # session.  This makes the table follow the actual RGB-D frame even
            # if the high-rate action-status mailbox briefly loses a Windows
            # file-lock race.  It also prevents an old JPEG from a previous run
            # from being displayed as current.
            telemetry_path = _docking_preview_telemetry_path(preview_path)
            if telemetry_path is not None:
                startup_phase = str(payload.get("phase", "") or "").upper()
                lifecycle_status = str(payload.get("status", "") or "").lower()
                waiting_for_first_frame = (
                    lifecycle_status in {"starting", "running"}
                    and startup_phase in {
                        "CAMERA_PROBE",
                        "CAMERA_OPEN",
                        "CAMERA_READY",
                        "LIVE_PREFLIGHT",
                        "LIVE_READY",
                        "CAMERA_STREAMING",
                    }
                    and not telemetry_path.is_file()
                )
                if waiting_for_first_frame:
                    frame_status = {}
                    payload["telemetry_read_error"] = "Waiting for first camera frame"
                elif not telemetry_path.is_file() and startup_phase == "CAMERA_OPEN_FAILED":
                    frame_status = {}
                    payload["telemetry_read_error"] = "No camera frame produced; see CAMERA_OPEN_FAILED message"
                else:
                    try:
                        frame_status = read_json_snapshot(telemetry_path, retries=3, retry_delay_s=0.001)
                        payload["telemetry_read_error"] = ""
                    except (OSError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
                        frame_status = {}
                        payload["telemetry_read_error"] = f"{type(exc).__name__}: {exc}"
                expected_session = str(payload.get("session_id", "") or "")
                frame_session = str(frame_status.get("session_id", "") or "") if isinstance(frame_status, dict) else ""
                if expected_session and frame_session == expected_session:
                    try:
                        frame_age = max(0.0, time.time() - float(frame_status.get("frame_updated_at", frame_status.get("updated_at", 0.0)) or 0.0))
                    except (TypeError, ValueError):
                        frame_age = float("inf")
                    if frame_age <= 3.0:
                        # Lifecycle fields may advance to RUNNING/CENTERLINE_*
                        # from the frame producer.  Keep identity fields from the
                        # action status and merge the actual frame/control values.
                        for field, value in frame_status.items():
                            if field not in {"action_id", "robot_ip", "started_at", "session_id"}:
                                payload[field] = value
                        payload["frame_age_sec"] = frame_age
                        payload["telemetry_source"] = "preview-frame"

            try:
                age = max(0.0, time.time() - float(payload.get("updated_at", 0.0) or 0.0))
            except (TypeError, ValueError):
                age = None
            payload["age_sec"] = age
            if payload.get("status") in {"starting", "running"} and age is not None and age > 5.0:
                payload.update(
                    status="stale",
                    phase="STALE",
                    message="Camera Docking heartbeat가 5초 이상 갱신되지 않았습니다.",
                )
            return payload, preview_path

        def _docking_value(value, *, digits=3, suffix=""):
            if value is None or value == "":
                return "-"
            try:
                return f"{float(value):.{digits}f}{suffix}"
            except (TypeError, ValueError):
                return str(value)

        def _render_seer_camera_docking_page(web, query):
            specs = [
                spec for spec in web._specs.values()
                if getattr(spec, "manufacturer", "") == "seer"
            ]
            if not specs:
                return render.page(
                    "Camera Docking",
                    '<div class="section"><h2>Camera Docking</h2><p>SEER AMR이 없습니다.</p></div>',
                    current="/camera",
                )
            selected_key = str((query or {}).get("robot", "") or "").strip()
            spec = next((item for item in specs if item.key == selected_key), specs[0])
            key = str(spec.key)
            status, preview_path = _read_camera_docking_status(web, key)
            status_text = str(status.get("status", "idle") or "idle")
            phase = str(status.get("phase", "IDLE") or "IDLE")
            message = str(status.get("message", "") or "")
            run_mode = str(status.get("run_mode", "-") or "-")
            age = status.get("age_sec")
            age_text = "-" if age is None else f"{float(age):.1f}s"
            active = status_text in {"starting", "running"}
            pill_class = "success" if status_text == "finished" else ("danger" if status_text in {"failed", "stale"} else "")

            options = "".join(
                f'<option value="{render.esc(item.key)}"{" selected" if item.key == key else ""}>'
                f'{render.esc(_seer_nickname_ip_label(item))}</option>'
                for item in specs
            )
            selector = (
                '<form method="get" action="/camera" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
                '<label>AMR <select name="robot">' + options + '</select></label>'
                '<input type="hidden" name="refresh" value="1">'
                '<button class="btn" type="submit">선택</button></form>'
            )

            robot_q = urllib.parse.quote(key, safe=":")
            stream_stamp = int(time.time() * 1000)
            preview_html = (
                '<div style="display:grid;gap:8px">'
                '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
                '<span class="status success"><span class="dot success"></span>실시간 RGB + Depth</span>'
                '<span class="muted">Standalone camera-only와 동일한 filtered tracker · RGB + Depth · 목표 30 FPS</span>'
                '</div>'
                f'<img id="seer-docking-live-preview" src="/seer/docking/stream.mjpg?robot={robot_q}&v={stream_stamp}" '
                'alt="RealSense live RGB plus depth filtered AprilTag docking stream" '
                'style="display:block;width:100%;max-width:1800px;height:auto;object-fit:contain;background:#111;border:1px solid var(--border);border-radius:12px">'
                '<small class="muted">기존 camera-only 창과 같은 corner median/EMA, pitch filter, IPPE 후보 안정화 결과를 왼쪽 RGB에 표시하고 오른쪽에는 RealSense depth colormap을 실시간으로 표시합니다.</small>'
                '</div>'
            )

            csrf = render.esc(web._csrf)
            return_to = f"/camera?robot={urllib.parse.quote(key, safe=':')}&refresh=1"
            preview_form = (
                '<form method="post" action="/seer/docking/action" data-dock-async="1" style="display:grid;gap:8px">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="robot" value="{render.esc(key)}">'
                '<input type="hidden" name="action_type" value="seerCameraDockPreview">'
                f'<input type="hidden" name="return_to" value="{render.esc(return_to)}">'
                '<label>미리보기 최대시간(초) <input type="number" name="max_runtime_s" value="300" min="1" max="1800" step="1"></label>'
                '<button class="btn" type="submit">알고리즘 미리보기 시작</button>'
                '<small class="muted">카메라/PnP/법선축/도킹 제어값만 계산하며 AMR에는 모션 명령을 보내지 않습니다.</small>'
                '</form>'
            )
            live_form = (
                '<form method="post" action="/seer/docking/action" data-dock-async="1" style="display:grid;gap:8px">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="robot" value="{render.esc(key)}">'
                '<input type="hidden" name="action_type" value="seerCameraDock">'
                f'<input type="hidden" name="return_to" value="{render.esc(return_to)}">'
                '<label>LIVE 최대시간(초) <input type="number" name="max_runtime_s" value="300" min="1" max="1800" step="1"></label>'
                '<label class="confirm"><input type="checkbox" name="confirm" required> 실제 AMR 이동 확인</label>'
                '<button class="btn danger" type="submit">도킹 시작 · Action</button>'
                '<small class="muted">seerCameraDock instantAction으로 실행합니다. PREVIEW가 실행 중이면 먼저 카메라를 정상 종료한 뒤 LIVE로 자동 전환합니다. LIVE 실행은 RGB+Depth 영상과 v/w·모터 RPM·SEER 속도를 자동 기록합니다.</small>'
                '</form>'
            )
            stage_forms = (
                '<div style="display:grid;gap:10px">'
                '<strong>단계별 1회 테스트 · 자동 복구 없음</strong>'
                '<small class="muted">각 버튼은 해당 단계만 한 번 실행하고 정지합니다. ①에서 LiDAR+RGB로 월드 중심축을 고정 저장하고, ②·③은 같은 축을 재사용합니다. 자동 후퇴/재접근 복구는 없습니다.</small>'
                '<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px">'
                '<form method="post" action="/seer/docking/action" data-dock-async="1" style="display:grid;gap:7px">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="robot" value="{render.esc(key)}">'
                '<input type="hidden" name="action_type" value="seerCameraDock">'
                '<input type="hidden" name="stage_mode" value="centerline">'
                '<input type="hidden" name="max_runtime_s" value="300">'
                f'<input type="hidden" name="return_to" value="{render.esc(return_to)}">'
                '<label class="confirm"><input type="checkbox" name="confirm" required> 실제 이동 확인</label>'
                '<button class="btn" type="submit">① 회전중심 맞추기</button>'
                '<small class="muted">LiDAR wall + RGB 태그 중심 7샘플로 월드 중심축을 먼저 고정한 뒤 회전중심을 ±2mm 안으로 맞춥니다. Yaw 정렬은 하지 않습니다.</small>'
                '</form>'
                '<form method="post" action="/seer/docking/action" data-dock-async="1" style="display:grid;gap:7px">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="robot" value="{render.esc(key)}">'
                '<input type="hidden" name="action_type" value="seerCameraDock">'
                '<input type="hidden" name="stage_mode" value="yaw">'
                '<input type="hidden" name="max_runtime_s" value="300">'
                f'<input type="hidden" name="return_to" value="{render.esc(return_to)}">'
                '<label class="confirm"><input type="checkbox" name="confirm" required> 실제 이동 확인</label>'
                '<button class="btn" type="submit">② 각도 맞추기 · 1°/s</button>'
                '<small class="muted">①에서 저장한 월드 중심축을 그대로 사용합니다. 카메라가 축을 다시 움직이지 않으며 현재 위치에서 Yaw만 ±1°/s로 정렬합니다.</small>'
                '</form>'
                '<form method="post" action="/seer/docking/action" data-dock-async="1" style="display:grid;gap:7px">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="robot" value="{render.esc(key)}">'
                '<input type="hidden" name="action_type" value="seerCameraDock">'
                '<input type="hidden" name="stage_mode" value="straight">'
                '<input type="hidden" name="max_runtime_s" value="300">'
                f'<input type="hidden" name="return_to" value="{render.esc(return_to)}">'
                '<label class="confirm"><input type="checkbox" name="confirm" required> 실제 이동 확인</label>'
                '<button class="btn" type="submit">③ 그대로 직진 · IMU/SEER hold</button>'
                '<small class="muted">카메라 조향 OFF. 저장된 월드 중심축 + SEER/IMU heading으로 작은 w 보정만 사용하고 tag_depth 약 0.50m에서 정지합니다.</small>'
                '</form>'
                '</div></div>'
            )
            stop_form = (
                '<form method="post" action="/seer/docking/action" style="display:grid;gap:8px">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="robot" value="{render.esc(key)}">'
                '<input type="hidden" name="action_type" value="seerCancelActiveAction">'
                f'<input type="hidden" name="return_to" value="{render.esc(return_to)}">'
                '<button class="btn warning" type="submit">도킹 / 미리보기 중지</button>'
                '<small class="muted">실행 중인 standalone Action을 취소하고 LIVE 도킹이면 안전정지를 요청합니다.</small>'
                '</form>'
            )

            status_rows = (
                f'<tr><th>Action 상태</th><td id="dock-status">{render._pill(status_text, pill_class)}</td><th>모드</th><td id="dock-mode">{render.esc(run_mode)}</td></tr>'
                f'<tr><th>Phase</th><td><code id="dock-phase">{render.esc(phase)}</code></td><th>업데이트</th><td id="dock-age">{render.esc(age_text)} 전</td></tr>'
                f'<tr><th>정렬 검증</th><td id="dock-verify">CENTER={"LOCKED" if status.get("centerline_verified") else "-"} · YAW={render.esc(_docking_value(status.get("yaw_hold_elapsed_s"), digits=1, suffix="s"))}/{render.esc(_docking_value(status.get("yaw_hold_required_s"), digits=1, suffix="s"))}</td><th>Straight latch</th><td id="dock-straight-latch">{"LOCKED" if status.get("straight_axis_latched") else "-"}</td></tr>'
                f'<tr><th>Tag tracker</th><td id="dock-tag">{"TRACKING / CTRL OK" if status.get("tag_visible") and status.get("tag_control_valid") else ("TRACKING / CTRL REJECT" if status.get("tag_visible") else (f"HOLD {float(status.get("tag_display_age_ms", 0.0) or 0.0):.0f} ms" if status.get("tag_display_held") else "-"))}</td><th>Axis lock</th><td id="dock-axis-lock">{"LOCKED" if status.get("axis_pose_locked") else "-"}</td></tr>'
                f'<tr><th>Axis distance</th><td id="dock-axis-distance">{render.esc(_docking_value(status.get("axis_distance_m"), suffix=" m"))}</td><th>Axis error</th><td id="dock-axis-error">{render.esc(_docking_value(None if status.get("axis_error_m") is None else float(status.get("axis_error_m"))*1000.0, suffix=" mm"))}</td></tr>'
                f'<tr><th>Yaw error</th><td id="dock-yaw">{render.esc(_docking_value(status.get("yaw_error_deg"), digits=2, suffix="°"))}</td><th>DU</th><td id="dock-du">{render.esc(_docking_value(status.get("du_error_px"), digits=1, suffix=" px"))}</td></tr>'
                f'<tr><th>Pose source</th><td id="dock-pose-source-table">{render.esc(str(status.get("display_pose_source", status.get("pose_measurement_source", "-")) or "-"))}</td><th>Pose quality</th><td id="dock-pose-quality">conf={render.esc(_docking_value(status.get("pose_measurement_confidence"), digits=2))} · plane={render.esc(_docking_value(status.get("wall_plane_rms_mm"), digits=2, suffix=" mm"))} · yaw σ={render.esc(_docking_value(status.get("pose_yaw_sigma_deg"), digits=2, suffix="°"))}</td></tr>'
                f'<tr><th>SEER localization</th><td id="dock-seer-loc">x={render.esc(_docking_value(status.get("seer_map_x_m"), digits=3, suffix=" m"))} · y={render.esc(_docking_value(status.get("seer_map_y_m"), digits=3, suffix=" m"))} · yaw={render.esc(_docking_value(status.get("seer_map_yaw_deg"), digits=2, suffix="°"))}</td><th>Localization score</th><td id="dock-seer-loc-score">{render.esc(_docking_value(status.get("seer_localization_score"), digits=2))}</td></tr>'
                f'<tr><th>수동 단계</th><td id="dock-manual-stage">{render.esc(str(status.get("manual_stage", "-") or "-"))}</td><th>직진 heading source</th><td id="dock-straight-source">{render.esc(str(status.get("straight_heading_source", "-") or "-"))}</td></tr>'
                f'<tr><th>WORLD AXIS</th><td id="dock-world-axis">{"LOCKED" if status.get("world_axis_locked") else "ACQUIRING"} · {render.esc(str(status.get("world_axis_lock_source", "-") or "-"))}</td><th>AXIS 샘플</th><td id="dock-world-axis-samples">{render.esc(str(status.get("world_axis_lock_samples", 0)))} / {render.esc(str(status.get("world_axis_lock_required_samples", 0)))}</td></tr>'
                f'<tr><th>직진 Target / Current</th><td id="dock-straight-heading">{render.esc(_docking_value(status.get("straight_target_heading_deg"), digits=3, suffix="°"))} / {render.esc(_docking_value(status.get("straight_current_heading_deg"), digits=3, suffix="°"))}</td><th>Heading error</th><td id="dock-straight-error">{render.esc(_docking_value(status.get("straight_heading_error_deg"), digits=3, suffix="°"))}</td></tr>'
                f'<tr><th>직선 이탈량</th><td id="dock-straight-cross">{render.esc(_docking_value(None if status.get("straight_cross_track_m") is None else float(status.get("straight_cross_track_m"))*1000.0, digits=1, suffix=" mm"))}</td><th>Camera steering</th><td id="dock-straight-camera">{"OFF" if status.get("straight_camera_steering") is False else "-"}</td></tr>'
                f'<tr><th>LiDAR wall</th><td id="dock-lidar-wall">dist={render.esc(_docking_value(status.get("lidar_wall_distance_m"), digits=3, suffix=" m"))} · rms={render.esc(_docking_value(status.get("lidar_wall_rms_mm"), digits=1, suffix=" mm"))}</td><th>LiDAR stream</th><td id="dock-lidar-stream">pts={render.esc(str(status.get("laser_point_count", 0) or 0))} · age={render.esc(_docking_value(status.get("laser_age_ms"), digits=0, suffix=" ms"))} · {render.esc(str(status.get("laser_source", "-") or "-"))}</td></tr>'
                f'<tr><th>LiDAR NetProtocol</th><td id="dock-lidar-param">laserStep={render.esc(str(status.get("laser_step_param", "-") if status.get("laser_step_param") is not None else "-"))} · attempts={render.esc("→".join(str(x) for x in (status.get("laser_attempts") or [])))}</td><th>LiDAR diagnostic</th><td id="dock-lidar-diag" style="font-size:12px;word-break:break-all">{render.esc(" | ".join(str(x) for x in (status.get("laser_diagnostics") or [])[-3:]) or "-")}</td></tr>'
                f'<tr><th>IMU attitude</th><td id="dock-imu-attitude">yaw={render.esc(_docking_value(status.get("imu_yaw_deg"), digits=2, suffix="°"))} · roll={render.esc(_docking_value(status.get("imu_roll_deg"), digits=2, suffix="°"))} · pitch={render.esc(_docking_value(status.get("imu_pitch_deg"), digits=2, suffix="°"))}</td><th>IMU fusion</th><td id="dock-imu-fusion">age={render.esc(_docking_value(status.get("imu_age_ms"), digits=0, suffix=" ms"))} · ω={render.esc(_docking_value(status.get("imu_yaw_rate_rps"), digits=4, suffix=" rad/s"))} · {render.esc(str(status.get("imu_yaw_fusion_source", "-") or "-"))}</td></tr>'
                f'<tr><th>Depth pose diagnostic</th><td id="dock-depth-pose">X={render.esc(_docking_value(status.get("rgbd_axis_distance_m"), digits=3, suffix=" m"))} · Y={render.esc(_docking_value(None if status.get("rgbd_axis_error_m") is None else float(status.get("rgbd_axis_error_m"))*1000.0, digits=1, suffix=" mm"))}</td><th>Fusion correction</th><td id="dock-fusion-correction">Δpos={render.esc(_docking_value(None if status.get("fusion_innovation_position_m") is None else float(status.get("fusion_innovation_position_m"))*1000.0, digits=1, suffix=" mm"))} · Δyaw={render.esc(_docking_value(status.get("fusion_innovation_yaw_deg"), digits=2, suffix="°"))}</td></tr>'
                f'<tr><th>Tag depth</th><td id="dock-depth">{render.esc(_docking_value(status.get("tag_depth_m"), suffix=" m"))}</td><th>Depth clearance</th><td id="dock-clearance">{render.esc(_docking_value(status.get("clearance_m"), suffix=" m"))}</td></tr>'
                f'<tr><th>Target v / w</th><td id="dock-target">{render.esc(_docking_value(status.get("target_v_mps")))} / {render.esc(_docking_value(status.get("target_w_rps")))}</td><th id="dock-sent-label">Sent v / w</th><td id="dock-sent">{render.esc(_docking_value(status.get("sent_v_mps")))} / {render.esc(_docking_value(status.get("sent_w_rps")))}</td></tr>'
                f'<tr><th>SEER vx / w</th><td id="dock-seer">{render.esc(_docking_value(status.get("seer_vx_mps")))} / {render.esc(_docking_value(status.get("seer_w_rps")))}</td><th>Safety</th><td id="dock-safety">blocked={int(bool(status.get("blocked", False)))} · emergency={int(bool(status.get("emergency", False)))}</td></tr>'
                f'<tr><th>Camera loop</th><td id="dock-camera-hz">{render.esc(_docking_value(status.get("camera_loop_hz"), digits=1, suffix=" Hz"))}</td><th>Web preview</th><td id="dock-preview-fps">{render.esc(_docking_value(status.get("preview_fps_actual"), digits=1, suffix=" FPS"))}</td></tr>'
                f'<tr><th>Camera profile</th><td id="dock-camera-profile">{render.esc(str(status.get("camera_profile", "-") or "-"))}</td><th>Camera probe</th><td id="dock-camera-probe">{render.esc(str(status.get("camera_probe_status", "-") or "-"))} · timeout={render.esc(_docking_value(status.get("camera_open_timeout_s"), digits=1, suffix=" s"))}</td></tr>'
                f'<tr><th>Preview age</th><td id="dock-preview-age">{render.esc(_docking_value(status.get("preview_age_ms"), digits=0, suffix=" ms"))}</td><th>Dropped preview</th><td id="dock-preview-drop">{render.esc(str(status.get("preview_drop_count", 0) if status.get("preview_drop_count") is not None else 0))}</td></tr>'
                f'<tr><th>Telemetry source</th><td id="dock-telemetry-source">{render.esc(str(status.get("telemetry_source", "action-status") or "action-status"))}</td><th>Status write errors</th><td id="dock-status-errors">{render.esc(str(status.get("status_write_error_count", 0) if status.get("status_write_error_count") is not None else 0))}</td></tr>'
                f'<tr><th>Telemetry read</th><td id="dock-telemetry-read">{render.esc(str(status.get("telemetry_read_error", "OK") or "OK"))}</td><th>Session</th><td id="dock-session">{render.esc(str(status.get("session_id", "-") or "-")[:12])}</td></tr>'
                f'<tr><th>CONTROL port</th><td id="dock-control-port">connected={"?" if status.get("control_connected") is None else int(bool(status.get("control_connected")))} · reconnect={render.esc(str(status.get("control_reconnect_count", "-") if status.get("control_reconnect_count") is not None else "-"))} · latency={render.esc(_docking_value(status.get("control_latency_ms"), digits=1, suffix=" ms"))}</td><th>CONTROL probe</th><td id="dock-control-probe">{render.esc(str(status.get("control_probe_response", "-") or "-"))}</td></tr>'
                f'<tr><th>CONTROL error</th><td id="dock-control-error">{render.esc(str(status.get("control_last_error", "-") or "-"))}</td><th>Motion error</th><td id="dock-motion-error">{render.esc(str(status.get("last_motion_error", "-") or "-"))}</td></tr>'
                f'<tr><th>Recording</th><td id="dock-recording">{"ON" if status.get("recording_active") else ("READY" if status.get("recording_enabled") else "OFF")}</td><th>Record session</th><td id="dock-recording-session">{render.esc(str(status.get("recording_session", "-") or "-"))}</td></tr>'
                f'<tr><th>Recorded video</th><td id="dock-recording-video">{render.esc(str(status.get("recording_video_frames", 0) if status.get("recording_video_frames") is not None else 0))} frames · drop {render.esc(str(status.get("recording_video_drop_count", 0) if status.get("recording_video_drop_count") is not None else 0))}</td><th>Command log</th><td id="dock-recording-log">{render.esc(str(status.get("recording_telemetry_rows", 0) if status.get("recording_telemetry_rows") is not None else 0))} rows · drop {render.esc(str(status.get("recording_telemetry_drop_count", 0) if status.get("recording_telemetry_drop_count") is not None else 0))}</td></tr>'
            )
            recording_entries = _docking_recording_entries(web, key, limit=12)
            recording_rows = []
            for entry in recording_entries:
                meta = entry.get("meta", {}) if isinstance(entry.get("meta", {}), Mapping) else {}
                session_name = str(entry.get("session", "") or "")
                session_q = urllib.parse.quote(session_name, safe="")
                started = str(meta.get("started_at_iso", "-") or "-")
                if "T" in started:
                    started = started.replace("T", " ")[:23]
                rec_status = str(meta.get("status", "recording") or "recording")
                rec_phase = str(meta.get("phase", "-") or "-")
                duration_raw = meta.get("duration_s")
                try:
                    duration_text = f"{float(duration_raw):.1f}s" if duration_raw is not None else "-"
                except (TypeError, ValueError):
                    duration_text = "-"
                frames = int(meta.get("video_frames", 0) or 0)
                video_drop = int(meta.get("video_drop_count", 0) or 0)
                rows = int(meta.get("telemetry_rows", 0) or 0)
                actions = []
                if entry.get("video_exists"):
                    actions.append(f'<a class="btn ghost" target="_blank" href="/seer/docking/replay?robot={robot_q}&session={session_q}">재생</a>')
                    actions.append(f'<a class="btn ghost" href="/seer/docking/recording/file?robot={robot_q}&session={session_q}&name=camera_commands.avi">AVI</a>')
                if entry.get("csv_exists"):
                    actions.append(f'<a class="btn ghost" href="/seer/docking/recording/file?robot={robot_q}&session={session_q}&name=telemetry.csv">CSV</a>')
                actions.append(f'<a class="btn ghost" href="/seer/docking/recording/file?robot={robot_q}&session={session_q}&name=session.json">JSON</a>')
                recording_rows.append(
                    '<tr>'
                    f'<td>{render.esc(started)}</td><td><code>{render.esc(session_name)}</code></td>'
                    f'<td>{render.esc(rec_status)} / {render.esc(rec_phase)}</td><td>{render.esc(duration_text)}</td>'
                    f'<td>{frames} frames · drop {video_drop}<br><small class="muted">{render.esc(_docking_human_bytes(entry.get("video_size", 0)))}</small></td>'
                    f'<td>{rows} rows<br><small class="muted">{render.esc(_docking_human_bytes(entry.get("csv_size", 0)))}</small></td>'
                    f'<td><div style="display:flex;gap:6px;flex-wrap:wrap">{"".join(actions)}</div></td>'
                    '</tr>'
                )
            recordings_html = (
                '<div class="section"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap">'
                '<div><h3 style="margin:0">LIVE 도킹 기록</h3><p class="muted" style="margin:5px 0 0">실제 도킹 때만 자동 기록합니다. 영상에는 RGB+Depth와 pose, sent v/w, 모터 L/R RPM, SEER vx/w, safety가 함께 찍히고 CSV에는 제어 루프 샘플을 저장합니다.</p></div>'
                '<span id="dock-recordings-count" class="muted">최근 기록 ' + str(len(recording_entries)) + '개</span></div>'
                '<div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>시작</th><th>Session</th><th>결과</th><th>시간</th><th>영상</th><th>명령 로그</th><th>보기 / 저장</th></tr></thead>'
                '<tbody id="dock-recordings-body">'
                + (''.join(recording_rows) if recording_rows else '<tr><td colspan="7" class="muted">아직 저장된 LIVE 도킹 기록이 없습니다.</td></tr>')
                + '</tbody></table></div></div>'
            )

            flash = render._flash(query or {})
            body = (
                f'{flash}<div class="section"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">'
                '<div><h2 style="margin:0">Camera Docking</h2>'
                '<p class="muted">RealSense + AprilTag · AMR 회전중심을 마커 중심 법선축에 먼저 맞추고 yaw 0° 정렬 후 최종 접근합니다.</p></div>'
                f'{selector}</div></div>'
                '<div class="section"><h3>도킹 Action</h3>'
                '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">'
                f'<div class="card">{preview_form}</div><div class="card">{live_form}</div><div class="card">{stop_form}</div>'
                f'<div class="card" style="grid-column:1/-1">{stage_forms}</div>'
                '</div></div>'
                '<div class="section"><h3>실시간 카메라 + 태그 기준 AMR 자세</h3>'
                '<p class="muted">왼쪽은 실제 RealSense RGB + Depth, 오른쪽은 AprilTag 중심 + 주변 벽 RGB-D 평면 + SEER 실제 속도를 융합해 재구성한 AMR Top view입니다. 작은 태그의 PnP 각도는 진단용 보조값으로만 표시합니다.</p>'
                '<div style="display:grid;grid-template-columns:minmax(480px,1.15fr) minmax(480px,1fr);gap:14px;align-items:stretch">'
                '<div class="card" style="padding:10px;overflow:hidden">' + preview_html + '</div>'
                '<div class="card" style="padding:10px;background:#111820;overflow:hidden">'
                '<canvas id="dock-pose-canvas" style="width:100%;height:430px;display:block;border-radius:10px;background:#111820"></canvas>'
                '<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:8px">'
                '<div style="padding:8px;border-radius:8px;background:#18222d"><div style="font-size:11px;color:#93a4b7">회전중심 거리 X</div><div id="dock-pose-x" style="font-size:1.15rem;font-weight:700;color:#fff">-</div></div>'
                '<div style="padding:8px;border-radius:8px;background:#18222d"><div style="font-size:11px;color:#93a4b7">중심축 오차 Y</div><div id="dock-pose-y" style="font-size:1.15rem;font-weight:700;color:#fff">-</div></div>'
                '<div style="padding:8px;border-radius:8px;background:#18222d"><div style="font-size:11px;color:#93a4b7">Yaw 틀어짐</div><div id="dock-pose-yaw" style="font-size:1.15rem;font-weight:700;color:#fff">-</div><div id="dock-pose-yaw-raw" style="font-size:10px;color:#7f91a5;margin-top:2px">PnP -</div></div>'
                '<div style="padding:8px;border-radius:8px;background:#18222d"><div style="font-size:11px;color:#93a4b7">Pose source</div><div id="dock-pose-source" style="font-size:.95rem;font-weight:700;color:#fff">-</div><div id="dock-pose-source-detail" style="font-size:10px;color:#7f91a5;margin-top:2px">RGB-D plane / PnP quality -</div></div>'
                '</div></div></div></div>'
                '<div class="section"><h3>실시간 도킹 상태</h3>'
                f'<p id="dock-message">{render.esc(message)}</p><div class="table-wrap"><table>{status_rows}</table></div></div>'
                f'{recordings_html}'
                '<div class="section"><h3>현재 기준</h3><p class="muted">camera_mount x=0.375 m, y=0.000 m · axis ±2 mm · DU ±10 px · yaw ±0.25° 목표. 도킹 계산은 카메라 영상 중심이 아니라 AprilTag 중심과 주변 벽 RGB-D 평면에서 구한 법선축, AMR 회전중심의 기하관계를 기준으로 합니다. RGB-D 평면이 불충분할 때만 PnP를 보수적으로 fallback 합니다.</p></div>'
            )
            status_url = f"/seer/docking/status.json?robot={robot_q}"
            stream_url = f"/seer/docking/stream.mjpg?robot={robot_q}"
            recordings_url = f"/seer/docking/recordings.json?robot={robot_q}"
            live_script = (
                '<script>(function(){'
                'var statusUrl=' + json.dumps(status_url) + ';'
                'var streamUrl=' + json.dumps(stream_url) + ';'
                'var recordingsUrl=' + json.dumps(recordings_url) + ';'
                'var robotKey=' + json.dumps(key) + ';'
                'function txt(id,v){var e=document.getElementById(id);if(e)e.textContent=(v===null||v===undefined||v==="")?"-":String(v);}'
                'function num(v,d,s,m){if(v===null||v===undefined||v==="")return "-";var n=Number(v);if(!Number.isFinite(n))return String(v);if(m)n*=m;return n.toFixed(d)+(s||"");}'
                'function pill(v){var e=document.getElementById("dock-status");if(!e)return;var c=(v==="finished")?"success":((v==="failed"||v==="stale")?"danger":"");e.textContent="";var s=document.createElement("span");s.className="status "+c;var d=document.createElement("span");d.className="dot "+c;s.appendChild(d);s.appendChild(document.createTextNode(String(v||"idle")));e.appendChild(s);}'
                'var img=document.getElementById("seer-docking-live-preview"),poseCanvas=document.getElementById("dock-pose-canvas"),lastPose=null,lastPreviewSeq=-1,lastPreviewAdvance=Date.now(),lastReconnect=0,actionRequestAtMs=0,requestedAction="";'
                'function reconnectStream(){if(!img)return;var now=Date.now();if(now-lastReconnect<800)return;lastReconnect=now;img.src=streamUrl+"&reconnect="+now;}'
                'function poseFinite(v){return v!==null&&v!==undefined&&v!==""&&Number.isFinite(Number(v));}'
                'function hasPose(d){return !!d&&poseFinite(d.axis_distance_m)&&poseFinite(d.axis_error_m)&&poseFinite(d.yaw_error_deg);}'
                'function poseArrow(ctx,x1,y1,x2,y2,color,w){var a=Math.atan2(y2-y1,x2-x1),h=9;ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=w||2;ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.beginPath();ctx.moveTo(x2,y2);ctx.lineTo(x2-h*Math.cos(a-Math.PI/6),y2-h*Math.sin(a-Math.PI/6));ctx.lineTo(x2-h*Math.cos(a+Math.PI/6),y2-h*Math.sin(a+Math.PI/6));ctx.closePath();ctx.fill();}'
                                'function drawPose(d){if(d&&hasPose(d))lastPose=d;if(!poseCanvas)return;d=lastPose||d||{};var r=poseCanvas.getBoundingClientRect(),W=Math.max(520,Math.round(r.width||760)),H=Math.max(390,Math.round(r.height||430)),pr=Math.min(2,window.devicePixelRatio||1);if(poseCanvas.width!==Math.round(W*pr)||poseCanvas.height!==Math.round(H*pr)){poseCanvas.width=Math.round(W*pr);poseCanvas.height=Math.round(H*pr);}var c=poseCanvas.getContext("2d");c.setTransform(pr,0,0,pr,0,0);c.clearRect(0,0,W,H);c.fillStyle="#111820";c.fillRect(0,0,W,H);var fx=function(v){return poseFinite(v)?Number(v):null;},x=fx(d.axis_distance_m),y=fx(d.axis_error_m),yaw=fx(d.yaw_error_deg),rawYaw=fx(d.pnp_yaw_error_deg),depth=fx(d.tag_depth_m),du=fx(d.du_error_px),ok=x!==null&&y!==null&&yaw!==null,held=!!d.tag_display_held&&!d.tag_visible;var wallX=64,cy=H*.56,right=W-42;c.strokeStyle="#e7edf5";c.lineWidth=4;c.beginPath();c.moveTo(wallX,35);c.lineTo(wallX,H-38);c.stroke();c.fillStyle="#17c96b";c.fillRect(wallX-7,cy-18,14,36);c.font="bold 14px sans-serif";c.fillStyle="#dce6f2";c.fillText("AprilTag",wallX+16,cy-28);c.strokeStyle="#2bd46e";c.lineWidth=3;c.setLineDash([10,8]);c.beginPath();c.moveTo(wallX+7,cy);c.lineTo(right,cy);c.stroke();c.setLineDash([]);c.fillStyle="#91a1b3";c.font="12px sans-serif";c.fillText("tag center normal axis / target centerline",wallX+95,cy-10);var goalX=wallX+Math.min(170,(right-wallX)*.28);c.strokeStyle="#24303c";c.lineWidth=7;c.beginPath();c.moveTo(goalX,35);c.lineTo(goalX,H-38);c.stroke();c.fillStyle="#91a1b3";c.fillText("goal standoff",goalX+9,52);if(!ok){c.fillStyle="#9baabc";c.font="15px sans-serif";c.fillText("AprilTag pose 계산 대기 중",Math.max(160,W*.43),H*.35);c.fillStyle="#657586";c.font="12px sans-serif";c.fillText("태그를 인식하면 AMR 위치/자세가 이 화면에 실시간으로 표시됩니다.",Math.max(120,W*.31),H*.42);return;}var camForward=poseFinite(d.camera_mount_x_m)?Number(d.camera_mount_x_m):.375,camLeft=poseFinite(d.camera_mount_y_m)?Number(d.camera_mount_y_m):0,maxX=Math.max(1.0,x+.55),maxY=Math.max(.38,Math.abs(y)+.28),scale=Math.min((right-wallX)/maxX,(H-100)/(2*maxY));scale=Math.max(100,Math.min(scale,350));var sx=wallX+x*scale,sy=cy-y*scale;var th=Math.PI+yaw*Math.PI/180,hx=Math.cos(th),hy=Math.sin(th),lx=-hy,ly=hx;function P(a,b){return [wallX+(x+a*hx+b*lx)*scale,cy-(y+a*hy+b*ly)*scale];}var bodyFront=Math.max(.43,camForward+.055),bodyRear=.35,bodyW=.54,pts=[[bodyFront,bodyW/2],[bodyFront,-bodyW/2],[-bodyRear,-bodyW/2],[-bodyRear,bodyW/2]],cam=P(camForward,camLeft);c.strokeStyle="#236dff";c.fillStyle="rgba(35,109,255,.20)";c.lineWidth=3;c.beginPath();for(var i=0;i<pts.length;i++){var q=P(pts[i][0],pts[i][1]);if(i===0)c.moveTo(q[0],q[1]);else c.lineTo(q[0],q[1]);}c.closePath();c.fill();c.stroke();var fa=P(bodyFront,bodyW/2),fb=P(bodyFront,-bodyW/2);c.strokeStyle="#72a6ff";c.lineWidth=4;c.beginPath();c.moveTo(fa[0],fa[1]);c.lineTo(fb[0],fb[1]);c.stroke();c.setLineDash([5,5]);c.strokeStyle="rgba(132,151,172,.38)";c.lineWidth=1.5;c.beginPath();c.arc(sx,sy,Math.max(42,bodyW*.60*scale),0,Math.PI*2);c.stroke();c.setLineDash([]);c.fillStyle="#3482ff";c.beginPath();c.arc(sx,sy,7,0,Math.PI*2);c.fill();var head=P(bodyFront+.09,0);poseArrow(c,sx,sy,head[0],head[1],"#7eaeff",3);c.fillStyle="#93b8ff";c.font="bold 11px sans-serif";c.fillText("머리 +X",Math.min(head[0]+7,W-68),head[1]-5);c.strokeStyle="#ffb316";c.lineWidth=2;c.setLineDash([6,4]);c.beginPath();c.moveTo(sx,sy);c.lineTo(cam[0],cam[1]);c.stroke();c.setLineDash([]);c.fillStyle="#ffb316";c.beginPath();c.arc(cam[0],cam[1],8,0,Math.PI*2);c.fill();c.strokeStyle="#ffd071";c.lineWidth=2;c.strokeRect(cam[0]-8,cam[1]-5,16,10);var mx=(sx+cam[0])/2,my=(sy+cam[1])/2;c.fillStyle="#ffc85c";c.font="bold 11px sans-serif";c.fillText("camera +X "+camForward.toFixed(3)+" m",Math.max(wallX+10,mx-65),my-8);c.fillStyle="#d9b56d";c.font="11px sans-serif";c.fillText("RealSense",cam[0]+10,cam[1]-8);var fovLen=Math.max(.22,Math.min(.42,Math.max(.10,x-camForward)*.55)),fovSpread=.13,cf1=P(camForward+fovLen,camLeft+fovSpread),cf2=P(camForward+fovLen,camLeft-fovSpread);c.strokeStyle="rgba(213,143,20,.55)";c.lineWidth=1.5;c.beginPath();c.moveTo(cam[0],cam[1]);c.lineTo(cf1[0],cf1[1]);c.moveTo(cam[0],cam[1]);c.lineTo(cf2[0],cf2[1]);c.stroke();c.strokeStyle="#ff394f";c.lineWidth=3;c.setLineDash([6,5]);c.beginPath();c.moveTo(sx,cy);c.lineTo(sx,sy);c.stroke();c.setLineDash([]);c.fillStyle="#ff5365";c.font="bold 12px sans-serif";c.fillText("rotation-center axis error "+Math.abs(y*1000).toFixed(1)+" mm",Math.min(sx+12,W-245),(sy+cy)/2-5);c.fillStyle="#c7d2df";c.font="bold 14px sans-serif";c.fillText("SBA-400EU",Math.min(sx+12,W-120),sy-4);c.font="12px sans-serif";c.fillStyle="#9fb0c3";c.fillText("rotation center",sx+12,sy+15);c.fillText("tag depth="+(depth!==null?depth.toFixed(3):"-")+" m",Math.max(wallX+85,cam[0]-30),cam[1]+23);c.fillStyle=held?"#ffb45b":"#70e1b1";c.font="bold 13px sans-serif";c.fillText((held?"HOLD":"LIVE")+"  X="+x.toFixed(3)+"m  Y="+(y*1000).toFixed(1)+"mm  Yaw="+yaw.toFixed(2)+"°",W-355,24);if(rawYaw!==null){c.fillStyle="#73879b";c.font="11px sans-serif";c.fillText("PnP yaw "+rawYaw.toFixed(2)+"°",W-155,43);}if(du!==null){c.fillStyle="#9fb0c3";c.font="12px sans-serif";c.fillText("DU "+du.toFixed(1)+" px",W-105,60);}c.strokeStyle="#506173";c.lineWidth=1;c.beginPath();c.moveTo(wallX+5,H-22);c.lineTo(sx,H-22);c.stroke();poseArrow(c,wallX+18,H-22,sx,H-22,"#506173",1);c.fillStyle="#91a1b3";c.fillText("tag → AMR rotation center  X="+x.toFixed(3)+" m",Math.max(wallX+20,(wallX+sx)/2-100),H-29);}'
'function update(d){var updatedMs=Number(d&&d.updated_at||0)*1000;if(actionRequestAtMs&&updatedMs&&updatedMs+250<actionRequestAtMs)return;if(actionRequestAtMs&&updatedMs>=actionRequestAtMs-250)actionRequestAtMs=0;pill(d.status);txt("dock-mode",d.run_mode||"-");txt("dock-phase",d.phase||"-");txt("dock-age",num(d.age_sec,1,"s 전"));txt("dock-message",d.message||"");txt("dock-verify","CENTER="+(d.centerline_verified?"LOCKED":"-")+" · YAW="+num(d.yaw_hold_elapsed_s,1,"s")+"/"+num(d.yaw_hold_required_s,1,"s"));txt("dock-straight-latch",d.straight_axis_latched?"LOCKED":"-");txt("dock-tag",d.tag_visible?(d.tag_control_valid?"TRACKING / CTRL OK":"TRACKING / CTRL REJECT"):(d.tag_display_held?("HOLD "+num(d.tag_display_age_ms,0," ms")):"-"));txt("dock-axis-lock",d.axis_pose_locked?"LOCKED":"-");txt("dock-axis-distance",num(d.axis_distance_m,3," m"));txt("dock-axis-error",num(d.axis_error_m,3," mm",1000));txt("dock-yaw",num(d.yaw_error_deg,2,"°"));txt("dock-du",num(d.du_error_px,1," px"));txt("dock-pose-source-table",d.display_pose_source||d.pose_measurement_source||"-");txt("dock-pose-quality","conf="+num(d.pose_measurement_confidence,2,"")+" · plane="+num(d.wall_plane_rms_mm,2," mm")+" · yaw σ="+num(d.pose_yaw_sigma_deg,2,"°"));txt("dock-seer-loc","x="+num(d.seer_map_x_m,3," m")+" · y="+num(d.seer_map_y_m,3," m")+" · yaw="+num(d.seer_map_yaw_deg,2,"°"));txt("dock-seer-loc-score",num(d.seer_localization_score,2,""));txt("dock-manual-stage",d.manual_stage||"-");txt("dock-world-axis",(d.world_axis_locked?"LOCKED":"ACQUIRING")+" · "+String(d.world_axis_lock_source||"-"));txt("dock-world-axis-samples",String(d.world_axis_lock_samples||0)+" / "+String(d.world_axis_lock_required_samples||0));txt("dock-straight-source",d.straight_heading_source||"-");txt("dock-straight-heading",num(d.straight_target_heading_deg,3,"°")+" / "+num(d.straight_current_heading_deg,3,"°"));txt("dock-straight-error",num(d.straight_heading_error_deg,3,"°"));txt("dock-straight-cross",num(d.straight_cross_track_m,1," mm",1000));txt("dock-straight-camera",d.straight_camera_steering===false?"OFF":"-");txt("dock-lidar-wall","dist="+num(d.lidar_wall_distance_m,3," m")+" · rms="+num(d.lidar_wall_rms_mm,1," mm"));txt("dock-lidar-stream","pts="+String(d.laser_point_count||0)+" · age="+num(d.laser_age_ms,0," ms")+" · "+String(d.laser_source||"-")+((d.laser_schema&&d.laser_schema!=="-")?("/"+d.laser_schema):"")+(d.laser_last_error?(" · "+d.laser_last_error):""));txt("dock-lidar-param","laserStep="+String((d.laser_step_param===null||d.laser_step_param===undefined)?"-":d.laser_step_param)+" · attempts="+((d.laser_attempts||[]).join("→")||"-"));txt("dock-lidar-diag",((d.laser_diagnostics||[]).slice(-3).join(" | ")||"-"));txt("dock-imu-attitude","yaw="+num(d.imu_yaw_deg,2,"°")+" · roll="+num(d.imu_roll_deg,2,"°")+" · pitch="+num(d.imu_pitch_deg,2,"°")+(d.imu_last_error?(" · "+d.imu_last_error):""));txt("dock-imu-fusion","age="+num(d.imu_age_ms,0," ms")+" · ω="+num(d.imu_yaw_rate_rps,4," rad/s")+" · "+String(d.imu_yaw_fusion_source||"-")+" · fused yaw="+num(d.fusion_map_yaw_deg,2,"°"));txt("dock-depth-pose","X="+num(d.rgbd_axis_distance_m,3," m")+" · Y="+num(d.rgbd_axis_error_m,1," mm",1000));txt("dock-fusion-correction","Δpos="+num(d.fusion_innovation_position_m,1," mm",1000)+" · Δyaw="+num(d.fusion_innovation_yaw_deg,2,"°")+(d.fusion_correction_accepted?" · accepted":" · hold"));txt("dock-depth",num(d.tag_depth_m,3," m"));txt("dock-clearance",num(d.clearance_m,3," m"));txt("dock-target",num(d.target_v_mps,3,"")+" / "+num(d.target_w_rps,3,""));txt("dock-sent-label",d.motion_transmitted?"SEER send v / w":"Preview applied v / w");txt("dock-sent",num(d.sent_v_mps,3,"")+" / "+num(d.sent_w_rps,3,""));txt("dock-seer",num(d.seer_vx_mps,3,"")+" / "+num(d.seer_w_rps,3,""));txt("dock-safety","blocked="+(d.blocked?1:0)+" · emergency="+(d.emergency?1:0));txt("dock-camera-hz",num(d.camera_loop_hz,1," Hz"));txt("dock-preview-fps",num(d.preview_fps_actual,1," FPS"));txt("dock-camera-profile",d.camera_profile||"-");txt("dock-camera-probe",(d.camera_probe_status||"-")+" · timeout="+num(d.camera_open_timeout_s,1," s"));txt("dock-preview-age",num(d.preview_age_ms,0," ms"));txt("dock-preview-drop",d.preview_drop_count===undefined?"0":d.preview_drop_count);txt("dock-telemetry-source",d.telemetry_source||"action-status");txt("dock-status-errors",d.status_write_error_count===undefined?"0":d.status_write_error_count);txt("dock-telemetry-read",d.telemetry_read_error||"OK");txt("dock-session",d.session_id?String(d.session_id).slice(0,12):"-");txt("dock-control-port","connected="+(d.control_connected===undefined||d.control_connected===null?"?":(d.control_connected?1:0))+" · reconnect="+(d.control_reconnect_count===undefined||d.control_reconnect_count===null?"-":d.control_reconnect_count)+" · latency="+num(d.control_latency_ms,1," ms"));txt("dock-control-probe",d.control_probe_response?JSON.stringify(d.control_probe_response):"-");txt("dock-control-error",d.control_last_error||"-");txt("dock-motion-error",d.last_motion_error||"-");txt("dock-recording",d.recording_active?"ON":(d.recording_enabled?"READY":"OFF"));txt("dock-recording-session",d.recording_session||"-");txt("dock-recording-video",String(d.recording_video_frames||0)+" frames · drop "+String(d.recording_video_drop_count||0));txt("dock-recording-log",String(d.recording_telemetry_rows||0)+" rows · drop "+String(d.recording_telemetry_drop_count||0));txt("dock-pose-x",num(d.axis_distance_m,3," m"));txt("dock-pose-y",num(d.axis_error_m,1," mm",1000));txt("dock-pose-yaw",num(d.yaw_error_deg,2,"°"));txt("dock-pose-yaw-raw","PnP "+num(d.pnp_yaw_error_deg,2,"°"));txt("dock-pose-source",d.display_pose_source||d.pose_measurement_source||"-");txt("dock-pose-source-detail",(d.pose_measurement_confidence===null||d.pose_measurement_confidence===undefined?"":"conf "+num(d.pose_measurement_confidence,2,""))+((d.wall_plane_rms_mm===null||d.wall_plane_rms_mm===undefined)?"":" · plane RMS "+num(d.wall_plane_rms_mm,2," mm"))+((d.pose_yaw_sigma_deg===null||d.pose_yaw_sigma_deg===undefined)?"":" · yaw σ "+num(d.pose_yaw_sigma_deg,2,"°")));drawPose(d);var seq=Number(d.preview_seq||0);if(seq!==lastPreviewSeq){lastPreviewSeq=seq;lastPreviewAdvance=Date.now();}else if((d.status==="starting"||d.status==="running")&&Date.now()-lastPreviewAdvance>2500){reconnectStream();lastPreviewAdvance=Date.now();}}'
                'function recLink(href,label,target){var a=document.createElement("a");a.className="btn ghost";a.href=href;a.textContent=label;if(target){a.target="_blank";a.rel="noopener";}return a;}'
                'function recCell(tr,text){var td=document.createElement("td");td.textContent=(text===null||text===undefined||text==="")?"-":String(text);tr.appendChild(td);return td;}'
                'function renderRecordings(p){var b=document.getElementById("dock-recordings-body"),c=document.getElementById("dock-recordings-count");if(!b)return;var a=(p&&Array.isArray(p.entries))?p.entries:[];if(c)c.textContent="최근 기록 "+a.length+"개";b.textContent="";if(!a.length){var tr0=document.createElement("tr"),td0=document.createElement("td");td0.colSpan=7;td0.className="muted";td0.textContent="아직 저장된 LIVE 도킹 기록이 없습니다.";tr0.appendChild(td0);b.appendChild(tr0);return;}a.forEach(function(e){var tr=document.createElement("tr"),s=String(e.session||""),q=encodeURIComponent(s),rq=encodeURIComponent(robotKey);recCell(tr,e.started||"-");var tdS=document.createElement("td"),code=document.createElement("code");code.textContent=s;tdS.appendChild(code);tr.appendChild(tdS);recCell(tr,(e.status||"recording")+" / "+(e.phase||"-"));recCell(tr,e.duration||"-");var tdV=recCell(tr,String(e.video_frames||0)+" frames · drop "+String(e.video_drop_count||0));var smV=document.createElement("small");smV.className="muted";smV.textContent=" · "+(e.video_size_text||"-");tdV.appendChild(smV);var tdC=recCell(tr,String(e.telemetry_rows||0)+" rows");var smC=document.createElement("small");smC.className="muted";smC.textContent=" · "+(e.csv_size_text||"-");tdC.appendChild(smC);var tdA=document.createElement("td"),wrap=document.createElement("div");wrap.style.display="flex";wrap.style.gap="6px";wrap.style.flexWrap="wrap";if(e.video_exists){wrap.appendChild(recLink("/seer/docking/replay?robot="+rq+"&session="+q,"재생",true));wrap.appendChild(recLink("/seer/docking/recording/file?robot="+rq+"&session="+q+"&name=camera_commands.avi","AVI",false));}if(e.csv_exists)wrap.appendChild(recLink("/seer/docking/recording/file?robot="+rq+"&session="+q+"&name=telemetry.csv","CSV",false));wrap.appendChild(recLink("/seer/docking/recording/file?robot="+rq+"&session="+q+"&name=session.json","JSON",false));tdA.appendChild(wrap);tr.appendChild(tdA);b.appendChild(tr);});}'
                'function pollRecordings(){fetch(recordingsUrl+"&_t="+Date.now(),{cache:"no-store",credentials:"same-origin"}).then(function(r){return r.ok?r.json():Promise.reject();}).then(renderRecordings).catch(function(){});}'
                'function poll(){fetch(statusUrl+"&_t="+Date.now(),{cache:"no-store",credentials:"same-origin"}).then(function(r){return r.ok?r.json():Promise.reject();}).then(update).catch(function(){});}'
                'function burstPoll(){[40,120,260,500,900,1500,2400].forEach(function(ms){setTimeout(poll,ms);});}'
                'function submitDockAction(form){var fd=new FormData(form),action=String(fd.get("action_type")||""),stage=String(fd.get("stage_mode")||""),btn=form.querySelector("button[type=submit]");requestedAction=action;actionRequestAtMs=Date.now();lastPreviewSeq=-1;lastPreviewAdvance=Date.now();pill("starting");txt("dock-mode",stage?("STAGE_"+stage.toUpperCase()):(action==="seerCameraDock"?"LIVE":"PREVIEW"));txt("dock-phase","REQUESTING");var stageMsg=stage==="centerline"?"회전중심 맞추기 1회 테스트 시작 · 자동 복구 없음":(stage==="yaw"?"각도 맞추기 1회 테스트 시작 · 1°/s · 자동 복구 없음":(stage==="straight"?"직진 1회 테스트 시작 · 카메라 조향 OFF · SEER+IMU heading hold":""));txt("dock-message",stageMsg||(action==="seerCameraDock"?"LIVE 도킹 요청 전송 중 · 기존 자세를 유지하면서 새 카메라 세션을 연결합니다":"미리보기 요청 전송 중 · 기존 자세를 유지하면서 새 카메라 세션을 연결합니다"));if(lastPose)drawPose(lastPose);reconnectStream();burstPoll();if(btn)btn.disabled=true;return fetch(form.action,{method:"POST",body:new URLSearchParams(fd),headers:{"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8"},credentials:"same-origin",cache:"no-store"}).then(function(r){var u=new URL(r.url,window.location.href),err=u.searchParams.get("err"),msg=u.searchParams.get("msg");if(err){actionRequestAtMs=0;pill("failed");txt("dock-phase","REQUEST_FAILED");txt("dock-message",err);}else if(msg){txt("dock-message",msg);}burstPoll();setTimeout(reconnectStream,120);}).catch(function(e){actionRequestAtMs=0;pill("failed");txt("dock-phase","REQUEST_FAILED");txt("dock-message","도킹 요청 전송 실패: "+String(e));}).finally(function(){if(btn)btn.disabled=false;});}'
                'document.querySelectorAll("form[data-dock-async]").forEach(function(form){form.addEventListener("submit",function(ev){ev.preventDefault();submitDockAction(form);});});'
                'setInterval(poll,100);poll();setInterval(pollRecordings,1000);pollRecordings();'
                'window.addEventListener("resize",function(){if(lastPose)drawPose(lastPose);});drawPose({});'
                'if(img){img.onerror=function(){setTimeout(reconnectStream,300);};}'
                '})();</script>'
            )
            return render.page(
                "Camera Docking",
                body + live_script,
                current="/camera",
                brand_title=f"SEER Camera Docking · {_seer_nickname_ip_label(spec)}",
            )

        def dispatch_get(web, handler):
            import urllib.parse

            path = urllib.parse.urlsplit(handler.path).path
            if path == "/camera":
                handler._html(200, _render_seer_camera_docking_page(web, handler._query()))
                return
            if path == "/seer/docking/status.json":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                spec = web._specs.get(key)
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler._json(404, {"error": "unknown SEER AMR"})
                    return
                status, _preview_path = _read_camera_docking_status(web, key)
                handler._json(200, status)
                return
            if path == "/seer/docking/recordings.json":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                spec = web._specs.get(key)
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler._json(404, {"error": "unknown SEER AMR"})
                    return
                result = []
                for entry in _docking_recording_entries(web, key, limit=12):
                    meta = entry.get("meta", {}) if isinstance(entry.get("meta", {}), Mapping) else {}
                    started = str(meta.get("started_at_iso", "-") or "-")
                    if "T" in started:
                        started = started.replace("T", " ")[:23]
                    duration_raw = meta.get("duration_s")
                    try:
                        duration = f"{float(duration_raw):.1f}s" if duration_raw is not None else "-"
                    except (TypeError, ValueError):
                        duration = "-"
                    result.append({
                        "session": str(entry.get("session", "") or ""),
                        "started": started,
                        "status": str(meta.get("status", "recording") or "recording"),
                        "phase": str(meta.get("phase", "-") or "-"),
                        "duration": duration,
                        "video_exists": bool(entry.get("video_exists")),
                        "csv_exists": bool(entry.get("csv_exists")),
                        "video_frames": int(meta.get("video_frames", 0) or 0),
                        "video_drop_count": int(meta.get("video_drop_count", 0) or 0),
                        "telemetry_rows": int(meta.get("telemetry_rows", 0) or 0),
                        "video_size_text": _docking_human_bytes(entry.get("video_size", 0)),
                        "csv_size_text": _docking_human_bytes(entry.get("csv_size", 0)),
                    })
                handler._json(200, {"entries": result})
                return
            if path == "/seer/docking/replay":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                session_name = str(query.get("session", "") or "").strip()
                spec = web._specs.get(key)
                folder = _safe_docking_recording_dir(web, key, session_name) if spec is not None and getattr(spec, "manufacturer", "") == "seer" else None
                if folder is None or not (folder / "camera_commands.avi").is_file():
                    handler._html(404, render.page("Docking Replay", '<div class="section"><h2>Docking Replay</h2><p>기록 영상을 찾을 수 없습니다.</p></div>', current="/camera"))
                    return
                try:
                    meta = read_json_snapshot(folder / "session.json", retries=2, retry_delay_s=0.001)
                except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
                robot_q = urllib.parse.quote(key, safe=":")
                session_q = urllib.parse.quote(session_name, safe="")
                stream_q = f"/seer/docking/recording/stream.mjpg?robot={robot_q}&session={session_q}&v={int(time.time()*1000)}"
                csv_q = f"/seer/docking/recording/file?robot={robot_q}&session={session_q}&name=telemetry.csv"
                avi_q = f"/seer/docking/recording/file?robot={robot_q}&session={session_q}&name=camera_commands.avi"
                body = (
                    '<div class="section"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap">'
                    f'<div><h2 style="margin:0">Docking Replay</h2><p class="muted"><code>{render.esc(session_name)}</code> · {render.esc(str(meta.get("status", "-") or "-"))} / {render.esc(str(meta.get("phase", "-") or "-"))}</p></div>'
                    f'<a class="btn ghost" href="/camera?robot={robot_q}">Camera Docking으로 돌아가기</a></div></div>'
                    '<div class="section"><h3>기록 영상 + 명령</h3>'
                    f'<img src="{render.esc(stream_q)}" alt="recorded docking replay" style="display:block;width:100%;max-width:1800px;height:auto;background:#111;border:1px solid var(--border);border-radius:12px">'
                    '<p class="muted">영상 아래 REC 밴드에 phase, 태그 pose, target/sent v·w, 좌/우 모터 명령 RPM, 실제 SEER vx/w, safety가 같은 시간축으로 기록되어 있습니다.</p>'
                    f'<div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn" href="{render.esc(csv_q)}">Telemetry CSV</a><a class="btn ghost" href="{render.esc(avi_q)}">AVI 원본</a></div></div>'
                )
                handler._html(200, render.page("Docking Replay", body, current="/camera", brand_title=f"SEER Docking Replay · {_seer_nickname_ip_label(spec)}"))
                return
            if path == "/seer/docking/recording/stream.mjpg":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                session_name = str(query.get("session", "") or "").strip()
                spec = web._specs.get(key)
                folder = _safe_docking_recording_dir(web, key, session_name) if spec is not None and getattr(spec, "manufacturer", "") == "seer" else None
                video_path = None if folder is None else folder / "camera_commands.avi"
                if video_path is None or not video_path.is_file():
                    handler.send_response(404); handler.send_header("Content-Length", "0"); handler.end_headers(); return
                try:
                    import cv2
                    cap = cv2.VideoCapture(str(video_path))
                    if not cap.isOpened():
                        cap.release(); handler.send_response(500); handler.send_header("Content-Length", "0"); handler.end_headers(); return
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 10.0)
                    fps = max(1.0, min(30.0, fps if fps > 0 else 10.0))
                    interval = 1.0 / fps
                    boundary = b"seerrecord"
                    handler.send_response(200)
                    handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=seerrecord")
                    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    next_frame_at = time.monotonic()
                    while True:
                        ok, frame = cap.read()
                        if not ok:
                            break
                        ok_jpg, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                        if not ok_jpg:
                            continue
                        data = encoded.tobytes()
                        handler.wfile.write(b"--" + boundary + b"\r\n")
                        handler.wfile.write(b"Content-Type: image/jpeg\r\n")
                        handler.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
                        handler.wfile.write(data); handler.wfile.write(b"\r\n"); handler.wfile.flush()
                        next_frame_at += interval
                        delay = next_frame_at - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
                    cap.release()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    try:
                        cap.release()
                    except Exception:
                        pass
                return
            if path == "/seer/docking/recording/file":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                session_name = str(query.get("session", "") or "").strip()
                name = str(query.get("name", "") or "").strip()
                spec = web._specs.get(key)
                folder = _safe_docking_recording_dir(web, key, session_name) if spec is not None and getattr(spec, "manufacturer", "") == "seer" else None
                allowed = {"camera_commands.avi": "video/x-msvideo", "telemetry.csv": "text/csv; charset=utf-8", "session.json": "application/json; charset=utf-8"}
                file_path = None if folder is None or name not in allowed else folder / name
                if file_path is None or not file_path.is_file():
                    handler.send_response(404); handler.send_header("Content-Length", "0"); handler.end_headers(); return
                try:
                    size = int(file_path.stat().st_size)
                    handler.send_response(200)
                    handler.send_header("Content-Type", allowed[name])
                    handler.send_header("Content-Length", str(size))
                    handler.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    handler.send_header("Cache-Control", "no-store")
                    handler.end_headers()
                    with file_path.open("rb") as src:
                        shutil.copyfileobj(src, handler.wfile, length=1024 * 1024)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            if path == "/seer/docking/stream.mjpg":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                spec = web._specs.get(key)
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler.send_response(404)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                status_path, preview_path = _docking_runtime_paths(web, key)
                if preview_path is None or status_path is None:
                    handler.send_response(404)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                boundary = b"seerframe"
                handler.send_response(200)
                handler.send_header("Content-Type", "multipart/x-mixed-replace; boundary=seerframe")
                handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                handler.send_header("Pragma", "no-cache")
                handler.send_header("Connection", "close")
                handler.end_headers()
                # IMPORTANT: do not poll/lock status + telemetry files inside
                # the MJPEG frame loop. On Windows those are exclusive sidecar
                # locks. The old implementation grabbed both ~80 times/sec per
                # browser stream, starving the Adapter writer. The symptom was
                # exactly what the real robot showed: RGB-D kept moving while
                # action status remained CAMERA_OPEN and every telemetry cell
                # stayed '-'. Capture the run start once, then validate each
                # JPEG only by its atomic file mtime/freshness. The 250 ms status
                # endpoint is the sole reader of status/telemetry mailboxes.
                try:
                    initial_status = read_json_snapshot(status_path, retries=3, retry_delay_s=0.001)
                except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                    initial_status = {}
                stream_started_at = float(initial_status.get("started_at", initial_status.get("updated_at", time.time())) or time.time())
                last_mtime_ns = -1
                last_frame_seen = time.monotonic()
                try:
                    while True:
                        try:
                            stat = preview_path.stat()
                            mtime_ns = int(stat.st_mtime_ns)
                            frame_mtime = float(stat.st_mtime)
                            # A frame older than this Action cannot belong to the
                            # current session even if deletion of the previous
                            # JPEG lost a Windows file race.
                            if frame_mtime + 0.05 < stream_started_at or time.time() - frame_mtime > 3.0:
                                if time.monotonic() - last_frame_seen > 3.0:
                                    return
                                time.sleep(0.02)
                                continue
                            if mtime_ns == last_mtime_ns:
                                # Do not leave a browser attached forever to a
                                # dead MJPEG response. The page watchdog will
                                # reconnect if the producer is still active.
                                if time.monotonic() - last_frame_seen > 3.0:
                                    return
                                time.sleep(0.02)
                                continue
                            data = read_bytes_snapshot(preview_path, retries=3, retry_delay_s=0.001)
                        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                            # If there is no fresh JPEG, end this MJPEG response
                            # instead of keeping a stale/broken stream forever.
                            # The browser watchdog reconnects and will attach as
                            # soon as the Adapter publishes a real camera frame.
                            if time.monotonic() - last_frame_seen > 3.0:
                                return
                            time.sleep(0.02)
                            continue
                        if not data:
                            time.sleep(0.012)
                            continue
                        try:
                            last_mtime_ns = int(preview_path.stat().st_mtime_ns)
                        except OSError:
                            last_mtime_ns = mtime_ns
                        last_frame_seen = time.monotonic()
                        handler.wfile.write(b"--" + boundary + b"\r\n")
                        handler.wfile.write(b"Content-Type: image/jpeg\r\n")
                        handler.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
                        handler.wfile.write(data)
                        handler.wfile.write(b"\r\n")
                        handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
            if path == "/seer/docking/preview.jpg":
                query = handler._query()
                key = str(query.get("robot", "") or "").strip()
                spec = web._specs.get(key)
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler.send_response(404)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                status_path, preview_path = _docking_runtime_paths(web, key)
                if status_path is None or preview_path is None or not preview_path.is_file():
                    handler.send_response(404)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                try:
                    current_status = read_json_snapshot(status_path, retries=3, retry_delay_s=0.001)
                    started_at = float(current_status.get("started_at", current_status.get("updated_at", 0.0)) or 0.0)
                    stat = preview_path.stat()
                    frame_mtime = float(stat.st_mtime)
                    if started_at <= 0.0 or frame_mtime + 0.05 < started_at or time.time() - frame_mtime > 3.0:
                        raise FileNotFoundError("no current-session camera frame")
                    data = read_bytes_snapshot(preview_path, retries=3, retry_delay_s=0.001)
                except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                    handler.send_response(404)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                handler.send_response(200)
                handler.send_header("Content-Type", "image/jpeg")
                handler.send_header("Content-Length", str(len(data)))
                handler.send_header("Cache-Control", "no-store, max-age=0")
                handler.end_headers()
                handler.wfile.write(data)
                return
            if path == "/seer/amr-labels":
                labels = {
                    str(spec.key): _seer_nickname_ip_label(spec)
                    for spec in web._specs.values()
                    if getattr(spec, "manufacturer", "") == "seer"
                }
                handler._json(200, {"labels": labels})
                return
            if path == "/vda5050":
                traces = getattr(web, "_seer_vda_traces", {})
                specs = [
                    spec
                    for spec in web._specs.values()
                    if getattr(spec, "manufacturer", "") == "seer"
                ]
                handler._html(
                    200,
                    render_vda5050_page(
                        render,
                        traces=traces,
                        senders=getattr(web, "_seer_vda_senders", {}),
                        specs=specs,
                        csrf=web._csrf,
                        query=handler._query(),
                    ),
                )
                return
            if path == "/recipe-builder/runtime":
                query = handler._query()
                seer_specs = [
                    spec for spec in web._specs.values() if getattr(spec, "manufacturer", "") == "seer"
                ]
                selected_key = str(query.get("robot", "") or "")
                if selected_key:
                    selected_spec = next(
                        (spec for spec in seer_specs if spec.key == selected_key),
                        seer_specs[0] if seer_specs else None,
                    )
                else:
                    running_specs = [
                        spec
                        for spec in seer_specs
                        if str(_read_builder_runtime(spec.key).get("status", "")) == "running"
                    ]
                    selected_spec = (
                        running_specs[0]
                        if len(running_specs) == 1
                        else (seer_specs[0] if seer_specs else None)
                    )
                if selected_spec is None:
                    handler._json(200, {"schema": 1, "status": "idle"})
                    return
                payload = {"schema": 1, "status": "idle"}
                candidate = _read_builder_runtime(selected_spec.key)
                if candidate:
                    payload = candidate
                try:
                    snapshot = web._monitor_snapshot(selected_spec.key)
                    paused = getattr(snapshot, "paused", None)
                    if paused is None:
                        paused = str(getattr(snapshot, "working_state", "") or "").upper() == "PAUSED"
                    payload = dict(payload)
                    payload["paused"] = bool(paused)
                except Exception:
                    payload = dict(payload)
                    payload.setdefault("paused", False)
                # BlockProgramExecutor publishes a 0.5 s heartbeat while it is
                # alive.  If the Adapter process disappeared without writing a
                # terminal state, do not leave the Builder Run button locked
                # forever on a stale ``running`` file.
                if str(payload.get("status", "")) == "running":
                    try:
                        runtime_age = max(0.0, time.time() - float(payload.get("updated_at", 0.0)))
                    except (TypeError, ValueError):
                        runtime_age = 0.0
                    if runtime_age > 5.0:
                        payload = dict(payload)
                        payload.update(
                            {
                                "status": "stopped",
                                "phase": "stale",
                                "message": "Block runtime heartbeat lost; controls unlocked",
                            }
                        )
                handler._json(200, payload)
                return
            if path == "/recipe-builder":
                query = handler._query()
                seer_specs = [
                    spec
                    for spec in web._specs.values()
                    if getattr(spec, "manufacturer", "") == "seer"
                ]
                selected_key = str(query.get("robot", "") or "")
                if selected_key:
                    selected_spec = next(
                        (spec for spec in seer_specs if spec.key == selected_key),
                        seer_specs[0] if seer_specs else None,
                    )
                else:
                    running_specs = [
                        spec
                        for spec in seer_specs
                        if str(_read_builder_runtime(spec.key).get("status", "")) == "running"
                    ]
                    selected_spec = (
                        running_specs[0]
                        if len(running_specs) == 1
                        else (seer_specs[0] if seer_specs else None)
                    )
                recipe_paths = getattr(web, "_seer_recipe_paths", {}) or {}
                recipes_path = (
                    recipe_paths.get(selected_spec.key)
                    if selected_spec is not None
                    else None
                ) or getattr(web, "_seer_recipes_path", None)
                if recipes_path is None:
                    handler._html(404, render.page("not found", "<p>선택한 SEER AMR의 recipes path를 찾을 수 없습니다.</p>"))
                    return
                map_view = (
                    _SEER_MAP_VIEWS.get(selected_spec.key, {})
                    if selected_spec is not None
                    else {}
                )
                builder_query = dict(query)
                if (
                    selected_spec is not None
                    and not str(builder_query.get("edit", "") or "").strip()
                    and not str(builder_query.get("delete", "") or "").strip()
                ):
                    runtime_state = _read_builder_runtime(selected_spec.key)
                    if str(runtime_state.get("status", "")) == "running":
                        active_recipe = str(runtime_state.get("recipe_name", "") or "").strip()
                        if active_recipe:
                            builder_query["edit"] = active_recipe
                handler._html(
                    200,
                    render_builder_page(
                        render,
                        web._csrf,
                        builder_query,
                        Path(recipes_path),
                        map_cache_path=map_view.get("cache_path"),
                        map_snapshot=(
                            web._monitor_snapshot(selected_spec.key)
                            if selected_spec is not None
                            else None
                        ),
                        map_position_unit=map_view.get("position_unit", "mm"),
                        map_robot_key=(selected_spec.key if selected_spec is not None else ""),
                        map_robot_options=tuple(
                            (spec.key, _seer_nickname_ip_label(spec))
                            for spec in seer_specs
                        ),
                    ),
                )
                return
            if path == "/fleet-map":
                specs = [
                    spec
                    for spec in web._specs.values()
                    if getattr(spec, "manufacturer", "") == "seer"
                ]
                if len(specs) < 2:
                    handler._redirect(
                        f"/adapter/{urllib.parse.quote(specs[0].key, safe=':')}"
                        if specs
                        else "/"
                    )
                    return
                query = handler._query()
                selected_key = str(query.get("robot", "") or "")
                selected = next((spec for spec in specs if spec.key == selected_key), specs[0])
                view = _SEER_MAP_VIEWS.get(selected.key)
                snapshot = web._monitor_snapshot(selected.key)
                if view is None or snapshot is None:
                    handler._html(
                        200,
                        render.page(
                            "SEER Fleet Map",
                            '<p class="error-notice">선택한 AMR의 지도 또는 상태가 아직 없습니다.</p>',
                            current="/fleet-map",
                        ),
                    )
                    return
                robot_views = []
                for spec in specs:
                    item_view = _SEER_MAP_VIEWS.get(spec.key, {})
                    robot_views.append(
                        {
                            "key": spec.key,
                            "label": _seer_nickname_ip_label(spec),
                            "snapshot": web._monitor_snapshot(spec.key),
                            "position_unit": item_view.get("position_unit", "mm"),
                            "orientation_unit": item_view.get("orientation_unit", "deg"),
                        }
                    )
                refresh = render._refresh_secs(query, 1)
                content = render_fleet_map_card(
                    view["cache_path"],
                    snapshot,
                    robot_views,
                    position_unit=view["position_unit"],
                    orientation_unit=view["orientation_unit"],
                    adapter_key=selected.key,
                    csrf_token=web._csrf,
                    active_route_path=view.get("active_route_path"),
                )
                handler._html(
                    200,
                    render.page(
                        "SEER Fleet Map",
                        render._with_poll(content, refresh),
                        current="/fleet-map",
                        brand_title="SEER Fleet Map",
                    ),
                )
                return
            if path == "/io":
                specs = [
                    spec
                    for spec in web._specs.values()
                    if getattr(spec, "manufacturer", "") == "seer"
                ]
                handler._html(
                    200,
                    _render_seer_io_page(
                        render, specs, web._csrf, handler._query()
                    ),
                )
                return
            return original_dispatch_get(web, handler)

        def _seer_binding_error(web, key: str) -> str:
            bindings = getattr(web, "_seer_member_bindings", {}) or {}
            binding = bindings.get(key)
            if not binding:
                return ""
            spec = web._specs.get(key)
            sender = getattr(web, "_seer_vda_senders", {}).get(key)
            if spec is None or sender is None:
                return "selected SEER AMR transport is unavailable"
            expected_ip = str(binding.get("vehicle_ip", "") or "")
            expected_serial = str(binding.get("serial", "") or "")
            actual_ip = str(getattr(spec, "vehicle_host", "") or "")
            actual_serial = str(getattr(spec, "serial", "") or "")
            trace_serial = str(
                getattr(getattr(getattr(sender, "trace", None), "identity", None), "serial_number", "")
                or ""
            )
            local_port = int(
                getattr(getattr(sender, "local_sender", None), "port", 0) or 0
            )
            expected_port = int(binding.get("control_ipc_port", 0) or 0)
            if actual_ip != expected_ip:
                return f"AMR IP routing mismatch: {actual_ip} != {expected_ip}"
            if actual_serial != expected_serial or trace_serial != expected_serial:
                return (
                    "VDA5050 identity routing mismatch: "
                    f"spec={actual_serial} trace={trace_serial} expected={expected_serial}"
                )
            if expected_port and local_port != expected_port:
                return f"AMR control IPC routing mismatch: {local_port} != {expected_port}"
            return ""

        def dispatch_post(web, handler, form):
            import urllib.parse

            path = urllib.parse.urlsplit(handler.path).path
            if path == "/seer/docking/action":
                key = str(form.get("robot", "") or "").strip()
                action_type = str(form.get("action_type", "") or "").strip()
                allowed = {"seerCameraDockPreview", "seerCameraDock", "seerCancelActiveAction"}
                spec = web._specs.get(key)
                target = f"/camera?robot={urllib.parse.quote(key, safe=':')}&refresh=1"
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler._redirect("/camera?err=" + urllib.parse.quote("unknown SEER AMR"))
                    return
                if action_type not in allowed:
                    handler._redirect(target + "&err=" + urllib.parse.quote("unsupported Camera Docking action"))
                    return
                binding_error = _seer_binding_error(web, key)
                if binding_error:
                    handler._redirect(target + "&err=" + urllib.parse.quote(binding_error))
                    return
                forwarded = dict(form)
                forwarded["action_type"] = action_type
                forwarded["return_to"] = target
                # Do not overwrite Adapter-owned runtime status before the
                # action has actually been accepted.  In particular, a LIVE
                # click while PREVIEW owns the camera must keep the real PREVIEW
                # heartbeat visible until the Adapter performs the handoff.
                web._post_action(handler, key, forwarded)
                return
            if path == "/recipe-builder/action":
                robot_key = str(form.get("robot", "") or "").strip()
                action_type = str(form.get("action_type", "") or "").strip()
                start_trace_id = str(form.get("start_trace_id", "") or "").strip()
                confirmed = str(form.get("confirm", "") or "").lower() in {"1", "true", "on", "yes"}
                if not confirmed:
                    handler._json(400, {"ok": False, "error": "실행 확인이 필요합니다."})
                    return
                source_user = "seer"
                try:
                    source_user = web._auth_user(handler.headers.get("Authorization"))
                except Exception:
                    pass
                action_parameters = (
                    {"_seer_start_trace_id": start_trace_id}
                    if start_trace_id
                    else None
                )
                delivered, message = _send_selected_builder_action(
                    web,
                    robot_key,
                    action_type,
                    source_user=source_user,
                    parameters=action_parameters,
                )
                try:
                    web._audit(
                        handler,
                        robot_key,
                        f"builder:{action_type}",
                        f"delivered={delivered} {message}",
                    )
                except Exception:
                    pass
                handler._json(
                    200 if delivered else 409,
                    {
                        "ok": bool(delivered),
                        "message": message if delivered else "",
                        "error": "" if delivered else message,
                        "robot": robot_key,
                        "action_type": action_type,
                        "start_trace_id": start_trace_id,
                    },
                )
                return
            if path == "/seer/map-refresh":
                key = str(form.get("key", "") or "").strip()
                spec = web._specs.get(key)
                target = f"/adapter/{urllib.parse.quote(key, safe=':')}"
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler._redirect("/?err=" + urllib.parse.quote("unknown SEER AMR"))
                    return
                binding_error = _seer_binding_error(web, key)
                if binding_error:
                    handler._redirect(target + "?err=" + urllib.parse.quote(binding_error))
                    return
                trace = getattr(web, "_seer_vda_traces", {}).get(key)
                sender = getattr(web, "_seer_vda_senders", {}).get(key)
                view = _SEER_MAP_VIEWS.get(key, {})
                cache_path = view.get("cache_path")
                before_mtime = None
                if cache_path is not None:
                    try:
                        before_mtime = Path(cache_path).stat().st_mtime_ns
                    except OSError:
                        pass
                if trace is None or sender is None:
                    handler._redirect(target + "?err=" + urllib.parse.quote("SEER map refresh transport unavailable"))
                    return
                payload = trace.factory.instant_action("seerRefreshMap")
                delivered, message = sender.send(
                    payload,
                    {"source_user": "webui", "confirmed": False, "created_at": time.time()},
                )
                if not delivered:
                    handler._redirect(target + "?err=" + urllib.parse.quote(message))
                    return
                refreshed = False
                if cache_path is not None:
                    deadline = time.monotonic() + 35.0
                    while time.monotonic() < deadline:
                        try:
                            current_mtime = Path(cache_path).stat().st_mtime_ns
                        except OSError:
                            current_mtime = None
                        if current_mtime is not None and (before_mtime is None or current_mtime > before_mtime):
                            refreshed = True
                            break
                        time.sleep(0.1)
                if refreshed:
                    suffix = "맵 최신화 완료"
                    handler._redirect(target + "?msg=" + urllib.parse.quote(suffix))
                else:
                    suffix = "맵 다운로드가 아직 완료되지 않았습니다. Adapter 로그의 SEER MAP DOWNLOAD RETRY를 확인하세요."
                    handler._redirect(target + "?err=" + urllib.parse.quote(suffix))
                return
            if path == "/seer/nickname":
                from dataclasses import replace

                key = str(form.get("key", "") or "").strip()
                nickname = str(form.get("nickname", "") or "").strip()[:40]
                spec = web._specs.get(key)
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    handler._redirect(
                        "/?err=" + urllib.parse.quote("unknown SEER AMR")
                    )
                    return
                ip = str(getattr(spec, "vehicle_host", "") or "").strip()
                if not ip:
                    handler._redirect(
                        "/?err=" + urllib.parse.quote("AMR IP를 확인할 수 없습니다")
                    )
                    return
                if not nickname:
                    nickname = f"SEER {getattr(spec, 'serial', ip)}"
                path_obj = getattr(web, "_seer_nickname_path", None)
                nicknames = dict(getattr(web, "_seer_nicknames", {}) or {})
                nicknames[ip] = nickname
                if path_obj is not None:
                    _save_amr_nicknames(Path(path_obj), nicknames)
                web._seer_nicknames = nicknames
                web._specs[key] = replace(spec, display_name=nickname)
                handler._redirect(
                    "/?msg="
                    + urllib.parse.quote(f"{ip} 닉네임 저장: {nickname}")
                )
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "adapter" and parts[2] in {"action", "manual"}:
                # Fleet adapter keys contain a colon (``seer:SERIAL``). Browsers
                # are allowed to percent-encode that path segment, while the
                # shared (13) WebUI dispatcher looks keys up literally. Decode
                # here so the selected AMR always resolves to its own sender.
                key_for_binding = urllib.parse.unquote(parts[1])
                binding_error = _seer_binding_error(web, key_for_binding)
                if binding_error:
                    target = f"/adapter/{urllib.parse.quote(key_for_binding, safe=':')}"
                    handler._redirect(target + "?err=" + urllib.parse.quote(binding_error))
                    return
                if parts[2] == "manual" and key_for_binding != parts[1]:
                    manual_spec = web._specs.get(key_for_binding)
                    if manual_spec is not None and getattr(manual_spec, "manufacturer", "") == "seer":
                        return web._post_manual(handler, key_for_binding, form)
            if (
                len(parts) == 3
                and parts[0] == "adapter"
                and parts[2] == "action"
                and str(form.get("action_type", "") or "") == "vdaOrderRoute"
            ):
                key = urllib.parse.unquote(parts[1])
                spec = web._specs.get(key)
                if spec is None or getattr(spec, "manufacturer", "") != "seer":
                    return original_dispatch_post(web, handler, form)

                # Named path navigation is emitted as a normal VDA5050 /order.
                # Free/reentry remain vendor instantActions because the unchanged
                # real-SEER order handler resolves named nodes rather than arbitrary
                # coordinate-only nodes.
                mode = str(form.get("navigation_mode", "path") or "path").strip().lower()
                if mode != "path":
                    return original_dispatch_post(web, handler, form)

                target = f"/adapter/{urllib.parse.quote(key, safe=':')}"
                return_to = str(form.get("return_to", "") or "").strip()
                if return_to.startswith("/") and not return_to.startswith("//"):
                    target = return_to
                separator = "&" if "?" in target else "?"
                if str(form.get("confirm", "") or "").lower() not in {
                    "1", "true", "on", "yes"
                }:
                    web._audit(handler, key, "order:seerPathNav", "rejected:unconfirmed")
                    handler._redirect(
                        target + separator + "err="
                        + urllib.parse.quote("VDA5050 order 실행 확인이 필요합니다")
                    )
                    return

                try:
                    target_id = str(form.get("id", "") or "").strip()
                    if not target_id:
                        raise ValueError("target node id is required")

                    raw_route = str(form.get("route_points", "") or "").strip()
                    route_points = []
                    if raw_route:
                        try:
                            decoded = json.loads(raw_route)
                            if not isinstance(decoded, list):
                                raise ValueError("route_points JSON must be an array")
                            route_points = [
                                str(value).strip()
                                for value in decoded
                                if str(value).strip()
                            ]
                        except json.JSONDecodeError:
                            route_points = [
                                token.strip()
                                for token in raw_route.replace("→", ",").split(",")
                                if token.strip()
                            ]
                    if not route_points:
                        source_id = str(form.get("source_id", "") or "").strip()
                        route_points = (
                            [source_id, target_id]
                            if source_id
                            and source_id != "SELF_POSITION"
                            and source_id != target_id
                            else [target_id]
                        )
                    if route_points[-1] != target_id:
                        raise ValueError(
                            f"route_points final node {route_points[-1]!r} does not match target {target_id!r}"
                        )

                    view = _SEER_MAP_VIEWS.get(key, {})
                    cache_path = view.get("cache_path")
                    position_unit = str(view.get("position_unit", "mm"))
                    orientation_unit = str(view.get("orientation_unit", "deg"))
                    model = read_map_cache(cache_path) if cache_path is not None else None
                    map_id = str(
                        model.get("map_name", "") if isinstance(model, dict) else ""
                    )

                    trace = getattr(web, "_seer_vda_traces", {}).get(key)
                    sender = getattr(web, "_seer_vda_senders", {}).get(key)
                    if trace is None or sender is None:
                        raise RuntimeError("SEER VDA5050 command transport is unavailable")

                    delay = float(form.get("waypoint_delay_sec", 0) or 0)
                    if delay < 0 or delay > 3600:
                        raise ValueError("waypoint_delay_sec must be between 0 and 3600")
                    node_actions = {}
                    if delay > 0 and len(route_points) > 2:
                        for node_id in route_points[1:-1]:
                            node_actions[node_id] = [
                                build_vda_action(
                                    "seerWait",
                                    {"seconds": delay},
                                    action_id=trace.factory.next_action_id("seerWait"),
                                    blocking_type="HARD",
                                )
                            ]

                    requested_order_id = str(form.get("order_id", "") or "").strip()
                    order_id = requested_order_id or trace.factory.next_order_id("PathNav")
                    binding = (getattr(web, "_seer_member_bindings", {}) or {}).get(key, {})
                    print(
                        "[SEER WEBUI ROUTE TARGET] "
                        f"key={key} ip={getattr(spec, 'vehicle_host', '')} "
                        f"serial={getattr(spec, 'serial', '')} "
                        f"ipc={binding.get('control_ipc_port', '-')} "
                        f"map={cache_path}"
                    )
                    payload = build_route_order(
                        trace.factory,
                        order_id=order_id,
                        route_points=route_points,
                        map_cache_path=cache_path,
                        position_unit=position_unit,
                        orientation_unit=orientation_unit,
                        map_id=map_id,
                        node_actions=node_actions,
                    )
                    if str(payload.get("serialNumber", "") or "") != str(getattr(spec, "serial", "") or ""):
                        raise RuntimeError("VDA5050 route serialNumber does not match selected AMR")
                    delivered, delivery_message = sender.send_order(
                        payload,
                        {
                            "source_user": "webui",
                            "confirmed": True,
                            "source": "path-nav",
                        },
                    )
                    if not delivered:
                        raise RuntimeError(delivery_message)
                    if len(route_points) > 1:
                        active_path = view.get("active_route_path")
                        if active_path is not None:
                            write_active_route(
                                Path(active_path),
                                route_points,
                                order_id=str(payload.get("orderId", "") or ""),
                            )
                    web._audit(
                        handler,
                        key,
                        "order:seerPathNav",
                        f"delivered=True nodes={len(payload['nodes'])} edges={len(payload['edges'])}",
                    )
                    transport_label = "Local VDA5050" if sender.mode == "local" else "FMS MQTT"
                    message = (
                        f"VDA5050 order {payload['orderId']} {transport_label} 전달 완료 · "
                        f"nodes={len(payload['nodes'])} edges={len(payload['edges'])}"
                    )
                    if node_actions:
                        message += f" · nodeActions={sum(len(v) for v in node_actions.values())}"
                    handler._redirect(
                        target + separator + "msg=" + urllib.parse.quote(message)
                    )
                except (ValueError, OSError, RuntimeError, ConnectionError, TypeError) as exc:
                    web._audit(handler, key, "order:seerPathNav", f"rejected:{exc}")
                    handler._redirect(
                        target + separator + "err=" + urllib.parse.quote(str(exc))
                    )
                return

            if path == "/vda5050":
                robot_key = str(form.get("robot", "") or "")
                target = f"/vda5050?robot={urllib.parse.quote(robot_key, safe=':')}"
                traces = getattr(web, "_seer_vda_traces", {})
                trace = traces.get(robot_key)
                if trace is None:
                    handler._redirect(
                        target + "&err=" + urllib.parse.quote("unknown SEER AMR")
                    )
                    return
                operation = str(form.get("operation", "publish") or "publish").strip().lower()
                if operation == "set_transport":
                    sender = getattr(web, "_seer_vda_senders", {}).get(robot_key)
                    if sender is None:
                        handler._redirect(
                            target + "&err=" + urllib.parse.quote("SEER VDA5050 sender unavailable")
                        )
                        return
                    try:
                        mode = sender.set_mode(str(form.get("transport", "mqtt") or "mqtt"))
                        label = "Local VDA5050" if mode == "local" else "FMS MQTT"
                        handler._redirect(
                            target + "&msg=" + urllib.parse.quote(f"WebUI 명령 전송 모드: {label}")
                        )
                    except ValueError as exc:
                        handler._redirect(target + "&err=" + urllib.parse.quote(str(exc)))
                    return
                if str(form.get("confirm", "")).lower() not in {
                    "1", "true", "on", "yes"
                }:
                    handler._redirect(
                        target + "&err=" + urllib.parse.quote("실제 MQTT 발행 확인이 필요합니다")
                    )
                    return
                try:
                    payload = json.loads(str(form.get("payload", "") or ""))
                    if not isinstance(payload, dict):
                        raise ValueError("JSON root must be an object")
                    suffix = str(form.get("topic", "") or "")
                    published = trace.publish_test(suffix, payload)
                    action = ""
                    if suffix == "instantActions":
                        actions = published.get("actions", [])
                        if actions and isinstance(actions[0], dict):
                            action = f" · {actions[0].get('actionType', '')}"
                    message = f"{suffix}{action} MQTT 발행 완료"
                    handler._redirect(
                        target + "&msg=" + urllib.parse.quote(message)
                    )
                except (ValueError, OSError, RuntimeError, ConnectionError) as exc:
                    handler._redirect(
                        target + "&err=" + urllib.parse.quote(str(exc))
                    )
                return
            if path != "/recipe-builder":
                return original_dispatch_post(web, handler, form)

            ajax_builder = str(form.get("ajax", "") or "").lower() in {"1", "true", "on", "yes"}
            builder_robot = str(form.get("robot", "") or "").strip()

            def builder_reply(*, ok: str = "", error: str = "", recipe_name: str = ""):
                if ajax_builder:
                    status = 200 if ok else 400
                    handler._json(
                        status,
                        {
                            "ok": bool(ok),
                            "message": str(ok or ""),
                            "error": str(error or ""),
                            "recipe_name": str(recipe_name or ""),
                            "robot": builder_robot,
                        },
                    )
                    return
                query = {"msg": ok} if ok else {"err": error}
                if recipe_name:
                    query["edit"] = recipe_name
                if builder_robot:
                    query["robot"] = builder_robot
                handler._redirect("/recipe-builder?" + urllib.parse.urlencode(query))

            recipe_paths = getattr(web, "_seer_recipe_paths", {}) or {}
            validators = getattr(web, "_seer_recipe_validators", {}) or {}
            appliers = getattr(web, "_seer_recipe_appliers", {}) or {}
            if recipe_paths and not builder_robot:
                builder_reply(error="Recipe를 저장/삭제할 AMR을 먼저 선택하세요")
                return
            recipes_path = recipe_paths.get(builder_robot) or getattr(web, "_seer_recipes_path", None)
            validator = validators.get(builder_robot) or getattr(web, "_seer_validate_recipe", None)
            applier = appliers.get(builder_robot) or getattr(web, "_seer_apply_recipe", None)
            if recipes_path is None or not callable(validator) or not callable(applier):
                builder_reply(error="선택한 AMR의 Block Builder가 초기화되지 않았습니다")
                return
            try:
                operation = str(form.get("operation", "save") or "save")
                if operation == "delete":
                    name = delete_recipe(
                        Path(recipes_path),
                        str(form.get("recipe_name", "") or ""),
                        validate=validator,
                    )
                    ok, message = applier()
                    if not ok:
                        builder_reply(
                            error=f"{name} 삭제 완료, Adapter 적용 실패: {message}",
                        )
                        return
                    builder_reply(
                        ok=f"{name} 삭제·적용 완료 · {message}",
                    )
                    return
                if operation != "save":
                    raise RecipeBuilderError("지원하지 않는 Block Builder 작업입니다")
                recipe = save_recipe(
                    Path(recipes_path),
                    str(form.get("definition", "")),
                    original_name=(
                        str(form.get("original_recipe_name", "") or "").strip()
                        or None
                    ),
                    validate=validator,
                )
                ok, message = applier()
                if not ok:
                    builder_reply(
                        error=f"{recipe['name']} 저장 완료, Adapter 적용 실패: {message}",
                        recipe_name=recipe["name"],
                    )
                    return
                variables = ", ".join(recipe.get("variables", ())) or "없음"
                builder_reply(
                    ok=f"{recipe['name']} 저장·적용 완료 · 실행 변수: {variables} · {message}",
                    recipe_name=recipe["name"],
                )
            except (OSError, RecipeBuilderError, ValueError) as exc:
                builder_reply(error=str(exc))

        def adapter_detail_page(spec, *args, **kwargs):
            render_spec = spec
            if getattr(spec, "manufacturer", "") == "seer":
                # The dashboard AppShell brand uses spec.display_name.  Render a
                # presentation-only copy so nickname changes are reflected as
                # "nickname - IP" without changing routing keys or VDA identity.
                render_spec = replace(spec, display_name=_seer_nickname_ip_label(spec))
            output = original_detail_page(render_spec, *args, **kwargs)
            if getattr(spec, "manufacturer", "") != "seer":
                return output
            output = (
                output.replace(
                    "MQTT live snapshot",
                    "SEER live state · local IPC",
                    1,
                )
                .replace(
                    "최근 adapter state payload 기준 운영 판단 값.",
                    "SEER TCP 상태를 Adapter가 1초마다 기록한 로컬 상태입니다. FMS MQTT와 독립적으로 갱신됩니다.",
                    1,
                )
                .replace("<td>MQTT broker</td>", "<td>FMS MQTT broker</td>", 1)
                .replace("<td>MQTT prefix</td>", "<td>FMS MQTT prefix</td>", 1)
            )
            # The three colored operational buttons occupy the first row.
            # Factsheet is intentionally less frequent and begins row two.
            output = output.replace(
                '<form id="act-factsheetRequest" class="action-card"',
                '<span class="seer-vehicle-action-break" aria-hidden="true"></span>'
                '<form id="act-factsheetRequest" class="action-card"',
                1,
            )
            return _decorate_seer_adapter_navigation(
                render, output, spec, current="dashboard"
            )

        def card_action_form(spec_key, action, csrf, return_to=""):
            action_type = str(getattr(action, "action_type", "") or "")
            if action_type in {"seerJackLoad", "seerJackUnload"}:
                # Render Jack as one stateful Vehicle action.  Keep both action
                # types in the VDA5050 capability list, but suppress the second
                # card and choose Load/Unload from the last controller-confirmed
                # Jack task plus the definitive DI2 lower-limit input.
                if action_type == "seerJackUnload":
                    return ""

                view = _SEER_MAP_VIEWS.get(spec_key, {})
                controller_state = (
                    read_controller_status_cache(view.get("controller_status_path"))
                    if view.get("controller_status_path") is not None else None
                )
                jack_supported = (
                    controller_state.get("jack_supported")
                    if isinstance(controller_state, dict) else None
                )
                model = (
                    str(controller_state.get("robot_model", "") or "").strip()
                    if isinstance(controller_state, dict) else ""
                )
                model_loaded = (
                    bool(controller_state.get("robot_model_loaded", False))
                    if isinstance(controller_state, dict) else False
                )
                model_error = (
                    str(controller_state.get("robot_model_query_error", "") or "").strip()
                    if isinstance(controller_state, dict) else ""
                )
                capability_reason = (
                    str(controller_state.get("jack_capability_reason", "") or "").strip()
                    if isinstance(controller_state, dict) else ""
                )
                model_html = (
                    f' · robot.model: {render.esc(model)}' if model else ""
                )
                if jack_supported is not True:
                    if jack_supported is False:
                        status_text = capability_reason or "이 AMR은 Jack 미지원"
                        button_text = "Jack 미지원"
                    elif model_loaded:
                        status_text = "robot.model 확인 결과 Jack 미지원"
                        button_text = "Jack 미지원"
                    elif model_error:
                        status_text = f"robot.model 조회 재시도 중: {model_error}"
                        button_text = "Jack 비활성화"
                    else:
                        status_text = "Jack 지원 여부 확인 중 · robot.model(API 1500) 조회 중"
                        button_text = "확인 중"
                    return (
                        '<article class="action-card seer-jack-vehicle-action is-disabled">'
                        '<span class="ac-info"><strong>SEER - Jack</strong>'
                        f'<span>{status_text}{model_html}</span></span>'
                        f'<button class="btn" type="button" disabled>{button_text}</button></article>'
                    )
                jack_state = read_jack_status(view.get("jack_status_path"))
                io_path = _SEER_IO_VIEWS.get(spec_key)
                io_snapshot = SeerIOCache.read(io_path) if io_path is not None else None
                di2 = None
                if isinstance(io_snapshot, dict):
                    for channel in io_snapshot.get("DI", []):
                        if not isinstance(channel, dict):
                            continue
                        try:
                            channel_id = int(channel.get("id", -1))
                        except (TypeError, ValueError):
                            continue
                        if channel_id == 2 and bool(channel.get("valid", True)):
                            di2 = bool(channel.get("status", False))
                            break

                phase = str((jack_state or {}).get("phase", "unknown") or "unknown").lower()
                # DI2=ON is a physical lower-limit and therefore overrides any
                # stale session cache.  DI2=OFF alone cannot prove full UP.
                if di2 is True:
                    phase = "down"
                elif phase == "down" and di2 is False:
                    phase = "unknown"

                moving = phase in {"moving_up", "moving_down"}
                if phase == "up":
                    state_label = "Jack 상태: 올라감 · 상승 완료 신호 확인"
                    next_action = "seerJackUnload"
                    button = "Jack 내리기"
                    variant = ""
                elif phase == "down":
                    state_label = "Jack 상태: 내려감 · DI2 ON"
                    next_action = "seerJackLoad"
                    button = "Jack 올리기"
                    variant = " primary"
                elif phase == "moving_up":
                    state_label = "Jack 상태: 올라가는 중... · TASK 완료 대기"
                    next_action = "seerJackLoad"
                    button = "올라가는 중..."
                    variant = " primary"
                elif phase == "moving_down":
                    state_label = "Jack 상태: 내려가는 중... · TASK 완료 + DI2 대기"
                    next_action = "seerJackUnload"
                    button = "내려가는 중..."
                    variant = ""
                elif phase == "failed":
                    state_label = "Jack 상태: 마지막 동작 실패 · 위치 확인 필요"
                    next_action = "seerJackUnload"
                    button = "Jack 내리기"
                    variant = ""
                else:
                    state_label = (
                        "Jack 상태: 확인 필요 · DI2 OFF (완전 상승 여부는 TASK로 확인)"
                        if di2 is False
                        else "Jack 상태: 확인 중..."
                    )
                    # When the upper position is unknown, returning to the known
                    # DI2 lower-limit is the only position the current hardware
                    # can independently verify.
                    next_action = "seerJackUnload"
                    button = "Jack 내리기"
                    variant = ""

                anchor = render.action_anchor("seerJackLoad")
                disabled = " disabled" if moving else ""
                confirm = (
                    '<span class="muted">동작 완료 전에는 다음 Jack 명령을 보낼 수 없습니다.</span>'
                    if moving
                    else '<label class="confirm"><input type="checkbox" name="confirm" required> 실행 확인</label>'
                )
                message = str((jack_state or {}).get("message", "") or "").strip()
                support_text = capability_reason or "Jack 지원 확인"
                support_detail = (
                    f'<span class="muted">{render.esc(support_text)} · robot.model: {render.esc(model)}</span>'
                    if model else f'<span class="muted">{render.esc(support_text)}</span>'
                )
                message_detail = (
                    f'<span class="muted">{render.esc(message)}</span>' if message else ""
                )
                detail = support_detail + message_detail
                return (
                    f'<form id="{anchor}" class="action-card seer-jack-vehicle-action" '
                    f'method="post" action="/adapter/{render.esc(spec_key)}/action">'
                    f'{render._csrf_field(csrf)}'
                    f'{render._return_to_field(render._anchored(return_to, "seerJackLoad"))}'
                    f'<input type="hidden" name="action_type" value="{next_action}">'
                    '<span class="ac-info"><strong>SEER - Jack</strong>'
                    f'<span>{render.esc(state_label)}</span>{detail}</span>'
                    f'{confirm}'
                    f'<button class="btn{variant}" type="submit"{disabled}>{button}</button></form>'
                )
            if action_type not in _SEER_RECIPE_ACTION_TYPES.get(spec_key, set()):
                return original_card_action_form(
                    spec_key, action, csrf, return_to=return_to
                )
            # Recipe quick cards must not execute with missing/default values.
            # They are safe bookmarks into the full Actions form where the
            # operator reviews parameters and the motion confirmation.
            anchor = render.action_anchor(action_type)
            label = render.esc(getattr(action, "label", action_type))
            href = f"/adapter/{render.esc(spec_key)}/actions#{anchor}"
            return (
                '<article class="action-card seer-action-bookmark">'
                f'<span class="ac-info"><strong>{label}</strong>'
                '<span>Actions 입력·확인 화면으로 이동 · 즉시 실행하지 않음</span></span>'
                f'<a class="btn" href="{href}">{label}</a></article>'
            )

        def actions_page(spec, *args, **kwargs):
            render_spec = (
                replace(spec, display_name=_seer_nickname_ip_label(spec))
                if getattr(spec, "manufacturer", "") == "seer" else spec
            )
            output = original_actions_page(render_spec, *args, **kwargs)
            return _decorate_seer_adapter_navigation(
                render, output, spec, current="actions"
            )

        def tests_page(spec, *args, **kwargs):
            render_spec = (
                replace(spec, display_name=_seer_nickname_ip_label(spec))
                if getattr(spec, "manufacturer", "") == "seer" else spec
            )
            output = original_tests_page(render_spec, *args, **kwargs)
            return _decorate_seer_adapter_navigation(
                render, output, spec, current="tests"
            )

        def logs_page(spec, *args, **kwargs):
            render_spec = (
                replace(spec, display_name=_seer_nickname_ip_label(spec))
                if getattr(spec, "manufacturer", "") == "seer" else spec
            )
            output = original_logs_page(render_spec, *args, **kwargs)
            return _decorate_seer_adapter_navigation(
                render, output, spec, current="logs"
            )

        def run_form(
            spec_key,
            action_type,
            label,
            motion,
            csrf,
            meta,
            parameters=(),
            acted=False,
            values=None,
        ):
            output = original_run_form(
                spec_key,
                action_type,
                label,
                motion,
                csrf,
                meta,
                parameters=parameters,
                acted=acted,
                values=values,
            )
            if not motion:
                return output
            confirmation = (
                '<label class="confirm"><input type="checkbox" '
                'name="confirm" required> 실제 이동 액션 실행 확인</label>'
            )
            return output.replace(
                '<button class="btn">실행</button>',
                confirmation + '<button class="btn primary">실행</button>',
                1,
            )

        def page(*args, **kwargs):
            output = original_page(*args, **kwargs)
            if "window.__seerPreserveFormsInstalled" in output:
                return output
            output = output.replace(
                "restart to apply",
                "SEER WebUI를 재시작하면 적용됩니다",
            )
            style = (
                '<style id="seer-static-flash">'
                '.ok,.error-notice{position:static!important;top:auto!important}'
                '.topbar>.seer-detail-heading{flex:0 0 100%;width:100%;'
                'justify-content:flex-start;order:3}'
                '@media (min-width:921px){'
                '.topnav{flex-wrap:nowrap;gap:2px}'
                '.topnav .nav-link{padding-left:8px;padding-right:8px}'
                '.head-actions{flex-wrap:nowrap}'
                '}'
                '.seer-vehicle-action-break{flex:0 0 100%;height:0;padding:0;margin:0}'
                '.seer-action-bookmark>.btn{width:100%;margin-top:auto;text-align:center}'
                '</style>'
            )
            script = f"<script>{_SEER_PRESERVE_FORM_INTERACTION_JS}</script>"
            return output.replace("</body>", style + script + "</body>", 1)

        def poll_script(seconds):
            script = original_poll_script(seconds)
            script = script.replace(
                "if(a&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName))return;",
                "if(a&&/^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)"
                "&&!(a.closest&&a.closest('.poll-controls')))return;",
            )
            script = script.replace(
                "window.__amrPollTick=function(){var a=document.activeElement;",
                "window.__amrPollTick=function(){"
                "if(window.__seerEmergencyPending||window.__seerFormPending"
                "||window.__seerMapDragging"
                "||document.querySelector('.seer-map-dragging')"
                "||document.querySelector('.seer-map-nav-dialog.is-open'))return;"
                "var a=document.activeElement;",
            )
            script = script.replace(
                "if(f&&c)c.innerHTML=f.innerHTML;",
                "if(f&&c&&!window.__seerEmergencyPending&&!window.__seerFormPending"
                "&&!window.__seerMapDragging"
                "&&!document.querySelector('.seer-map-dragging')"
                "&&!document.querySelector('.seer-map-nav-dialog.is-open'))"
                "c.innerHTML=f.innerHTML;",
            )
            # The original WebUI gives this script its CSP nonce. Keep the map
            # interaction code in the same executable block: browsers do not
            # execute script tags in live-state HTML inserted via innerHTML.
            return script.replace(
                "</script>",
                SEER_MAP_INTERACTION_JS
                + "\n"
                + _SEER_EMERGENCY_INTERACTION_JS
                + "\n</script>",
                1,
            )

        def refresh_secs(q, default=5):
            # Original list/camera pages hard-code a 5-second default instead
            # of reading [web_ui]. Keep every SEER page at the same 1-second
            # display default while preserving an explicit ?refresh= value.
            selected_default = 1 if not q or "refresh" not in q else default
            return original_refresh_secs(q, selected_default)

        def instant_action_cards(snapshot):
            """Hide administrative FINISHED spam and keep one card per action type."""

            states = tuple(getattr(snapshot, "instant_action_states", None) or ())
            if not states:
                return ""
            hidden_types = {
                "cancelOrder",
                "clearErrors",
                "clearInstantActions",
                "seerCancelActiveAction",
                "seerResetActionErrors",
                "seerRefreshMap",
                "stateRequest",
                "factsheetRequest",
            }
            latest_by_type = {}
            order = []
            for state in states:
                if not isinstance(state, dict):
                    continue
                action_type = str(state.get("actionType", "") or "").strip()
                if not action_type or action_type in hidden_types:
                    continue
                if action_type not in latest_by_type:
                    order.append(action_type)
                latest_by_type[action_type] = state
            compact_states = [latest_by_type[action_type] for action_type in order]
            if not compact_states:
                return ""

            class SnapshotProxy:
                instant_action_states = compact_states

            return original_instant_action_cards(SnapshotProxy())

        def live_table(spec, snapshot):
            if getattr(spec, "manufacturer", "") != "seer" or snapshot is None:
                return original_live(spec, snapshot)
            if not getattr(snapshot, "connection_state", None):
                snapshot.connection_state = (
                    "ONLINE" if getattr(snapshot, "adapter_online", True) else "OFFLINE"
                )
            if getattr(snapshot, "paused", None) is None:
                snapshot.paused = (
                    str(getattr(snapshot, "working_state", "") or "").upper()
                    == "PAUSED"
                )
            blocked = bool(
                getattr(snapshot, "blocked", False)
                or getattr(snapshot, "field_violation", False)
                or str(getattr(snapshot, "working_state", "") or "").upper()
                == "BLOCKED"
            )
            output = original_live(spec, snapshot)
            if hasattr(snapshot, "acs_broker_connected"):
                connected = bool(snapshot.acs_broker_connected)
                old_acs = render._tele(
                    "ACS broker",
                    "connected" if connected else "disconnected",
                    "" if connected else "warn",
                )
                new_acs = render._tele(
                    "FMS MQTT",
                    "CONNECTED" if connected else "OFFLINE · local OK",
                    "good" if connected else "warn",
                )
                output = output.replace(old_acs, new_acs, 1)
            paused_card = render._tele("paused", snapshot.paused)
            output = output.replace(
                paused_card,
                render._tele(
                    "paused",
                    "PAUSED" if snapshot.paused else "RUNNING",
                    "warn" if snapshot.paused else "good",
                ),
                1,
            )
            blocked_card = render._tele(
                "blocked", "BLOCKED" if blocked else "CLEAR", "bad" if blocked else "good"
            )
            view = _SEER_MAP_VIEWS.get(getattr(spec, "key", ""))
            if view is None:
                grid_end = output.rfind("</div>")
                if grid_end >= 0:
                    return output[:grid_end] + blocked_card + output[grid_end:]
                return output + blocked_card
            marker = '<div class="map-card">'
            prefix = output[: output.rfind(marker)] if marker in output else output
            grid_end = prefix.rfind("</div>")
            if grid_end >= 0:
                prefix = prefix[:grid_end] + blocked_card + prefix[grid_end:]
            else:
                prefix += blocked_card
            return prefix + render_map_card(
                view["cache_path"],
                snapshot,
                position_unit=view["position_unit"],
                orientation_unit=view["orientation_unit"],
                adapter_key=spec.key,
                csrf_token=str(view.get("csrf", "")),
                return_to=f"/adapter/{spec.key}",
                active_route_path=view.get("active_route_path"),
                controller_status=read_controller_status_cache(
                    view.get("controller_status_path")
                ) if view.get("controller_status_path") is not None else None,
            )

        def emergency_forms(spec, csrf, return_to="", snapshot=None):
            if getattr(spec, "manufacturer", "") != "seer":
                return original_emergency(
                    spec, csrf, return_to=return_to, snapshot=snapshot
                )
            by_type = {
                getattr(action, "action_type", ""): action
                for action in spec.instant_actions
            }
            action = by_type.get("seerEmergencySwitch")
            parts = []
            if action is not None:
                raw = str(getattr(snapshot, "active_emergency_stop", "") or "NONE")
                active = raw.upper() not in ("", "NONE")
                accessibility_state = "활성 상태" if active else "비활성 상태"
                button_label = "비상정지 해제" if active else "비상정지"
                help_text = (
                    "현재 비상정지 상태입니다. 해제해도 이전 작업은 재개되지 않습니다."
                    if active
                    else "누르면 즉시 정지하고 진행 중인 작업을 취소합니다."
                )
                button_title = (
                    "누르면 비상정지를 즉시 해제합니다. 이전 작업은 재개되지 않습니다."
                    if active
                    else "누르면 비상정지를 즉시 활성화하고 진행 작업을 취소합니다."
                )
                button_style = (
                    "background:#13795b;color:#fff;border-color:#46d7a4;"
                    "box-shadow:0 0 0 2px rgba(70,215,164,.14)"
                    if active
                    else "background:#b4232f;color:#fff;border-color:#ff7b86;"
                    "box-shadow:0 0 0 2px rgba(255,91,105,.18)"
                )
                parts.append(
                    f'<form class="estop" data-seer-estop-form method="post" '
                    f'action="/adapter/{render.esc(spec.key)}/action">'
                    f'{render._csrf_field(csrf)}{render._return_to_field(return_to)}'
                    '<input type="hidden" name="action_type" value="seerEmergencySwitch">'
                    f'<small class="muted estop-help" style="display:block;margin-bottom:6px;'
                    f'max-width:100%;line-height:1.35;white-space:normal">{help_text}</small>'
                    f'<button class="btn estop" type="submit" aria-pressed="{str(active).lower()}" '
                    f'aria-label="비상정지 {accessibility_state}" '
                    f'style="{button_style}" title="{button_title}">{button_label}</button></form>'
                )
            motor = render._motor_control_card(
                spec, by_type, csrf, return_to=return_to, snapshot=snapshot
            )
            if motor:
                parts.append(motor)
            return "".join(parts)

        def jog_card(spec, *args, **kwargs):
            if getattr(spec, "manufacturer", "") != "seer":
                return original_jog(spec, *args, **kwargs)
            snapshot = kwargs.get("snapshot")
            csrf = str(kwargs.get("csrf", ""))
            limits = ManualDriveLimits.from_env()
            key = render.esc(spec.key)
            point_names = []
            view = _SEER_MAP_VIEWS.get(getattr(spec, "key", ""))
            model = read_map_cache(view["cache_path"]) if view is not None else None
            if isinstance(model, dict):
                point_names = sorted(
                    {
                        str(item.get("name", "")).strip()
                        for item in model.get("advanced_points", [])
                        if isinstance(item, dict) and str(item.get("name", "")).strip()
                    }
                )
            point_list_id = render.esc(f"seer-path-points-{spec.key}")
            point_options = "".join(
                f'<option value="{render.esc(name)}"></option>' for name in point_names
            )
            # Blank source emits a one-node VDA5050 target order. A named source
            # emits a two-node source->target order. Both use the selected
            # WebUI VDA5050 transport (FMS MQTT by default, Local fallback on demand).
            source_default = ""
            motor_state = (
                render._motor_label(snapshot) if snapshot is not None else "모터 상태 미상"
            )
            pad = (
                '<div class="jog-pad">'
                '<button type="button" class="btn jog-btn jog-up" data-dir="up" title="전진" disabled>&#9650;</button>'
                '<button type="button" class="btn jog-btn jog-left" data-dir="left" title="좌회전" disabled>&#9668;</button>'
                '<button type="button" class="btn danger jog-stop" title="정지">STOP</button>'
                '<button type="button" class="btn jog-btn jog-right" data-dir="right" title="우회전" disabled>&#9658;</button>'
                '<button type="button" class="btn jog-btn jog-down" data-dir="down" title="후진" disabled>&#9660;</button>'
                '</div>'
            )
            path_nav = (
                f'<form class="action-card" method="post" action="/adapter/{key}/action">'
                f'{render._csrf_field(csrf)}{render._return_to_field(str(kwargs.get("return_to", "")))}'
                '<input type="hidden" name="action_type" value="vdaOrderRoute">'
                '<h3>Path Nav · VDA5050 /order</h3>'
                '<p class="muted">실물 SEER와 Simulator 모두 같은 VDA5050 order의 nodes[]/edges[]로 이동합니다. '
                'Source를 비우면 목표 node 하나의 order를 발행하고, Source를 지정하면 source→target 두 node order를 발행합니다.</p>'
                f'<datalist id="{point_list_id}">{point_options}</datalist>'
                '<div class="command-fields">'
                '<label class="mini-field">Target point '
                f'<input name="id" list="{point_list_id}" required maxlength="128" '
                'placeholder="LM1" autocomplete="off"></label>'
                '<label class="mini-field">Source point (optional) '
                f'<input name="source_id" list="{point_list_id}" maxlength="128" '
                f'value="{render.esc(source_default)}" placeholder="비우면 현재 위치 자동" autocomplete="off"></label>'
                '<label class="mini-field">Order ID (optional) '
                '<input name="order_id" maxlength="128" placeholder="비우면 WebUI가 자동 생성"></label></div>'
                '<label class="confirm"><input type="checkbox" name="confirm" required> '
                'VDA5050 order 전송 및 실제 이동 실행 확인</label>'
                '<button class="btn primary">Send VDA5050 Order</button></form>'
            )
            return (
                '<div class="cmd-wrap"><div class="manual jog-card action-card" '
                f'data-key="{key}" data-seer="1" data-trans="{limits.default_linear_mps}" '
                f'data-rot="{limits.default_angular_deg_s}" data-speed="{limits.default_linear_mps}">'
                f'<input type="hidden" id="mc-csrf" value="{render.esc(csrf)}">'
                '<div class="jog-head"><strong>키보드 / 버튼 수동조작</strong>'
                f'<span class="ac-state">현재: {render.esc(motor_state)}</span></div>'
                '<label class="confirm jog-arm-control" style="display:flex;align-items:center;gap:7px;margin:8px 0 10px">'
                '<input type="checkbox" name="manual_armed"> '
                '<strong>수동조작 활성화</strong> <span class="muted" data-jog-arm-state>수동조작 OFF</span></label>'
                f'{pad}'
                '<div class="command-fields">'
                '<label class="mini-field">전후진 속도 (m/s) '
                f'<input type="number" name="linear_speed" value="{limits.default_linear_mps}" '
                f'min="0.01" max="{limits.max_linear_mps}" step="0.01"></label>'
                '<label class="mini-field">회전 속도 (deg/s) '
                f'<input type="number" name="angular_speed_deg" value="{limits.default_angular_deg_s}" '
                f'min="1" max="{limits.max_angular_deg_s}" step="1"></label></div>'
                '<p class="muted">수동조작 활성화를 직접 체크해야 방향키/버튼 조작이 가능합니다. '
                '체크 후 30초 동안 방향키·버튼·속도 입력이 없으면 자동 OFF됩니다. 다른 브라우저 창이나 탭으로 포커스를 옮겨도 주행 중인 명령을 임의로 끊지 않으며, 실제 페이지를 닫거나 이동할 때만 안전정지합니다. 같은 화면의 카드 자동 갱신 중에는 유지됩니다. '
                '방향키를 누르는 동안 명령을 유지하고 키를 떼면 즉시 정지하며, 전진/후진과 좌/우 회전을 동시에 누를 수 있습니다.</p>'
                '<p class="muted">속도 입력칸은 숫자를 전부 지운 뒤 새 값을 입력할 수 있습니다. '
                '빈 값 편집 중에는 수동주행 명령을 보내지 않으며, 입력한 값은 화면을 이동해도 유지됩니다.</p>'
                f'<p class="muted">WebUI 안전 범위: 선속도 0.01–{limits.max_linear_mps:g} m/s, '
                f'각속도 1–{limits.max_angular_deg_s:g} deg/s. 장비 모델 제한을 넘지 않도록 '
                f'환경변수로 더 낮출 수 있습니다.</p></div>{path_nav}</div>'
            )

        render._mqtt_live_table = live_table
        render._instant_action_cards = instant_action_cards
        render._jog_card = jog_card
        render._emergency_forms = emergency_forms
        render._poll_script = poll_script
        render._refresh_secs = refresh_secs
        render._run_form = run_form
        render._card_action_form = card_action_form
        render.adapter_list_page = adapter_list_page
        render._nav = nav
        render.page = page
        render.adapter_detail_page = adapter_detail_page
        render.actions_page = actions_page
        render.tests_page = tests_page
        render.logs_page = logs_page
        server.WebUi._dispatch_get = dispatch_get
        server.WebUi._dispatch_post = dispatch_post
        render._JOG_JS = _SEER_AWARE_JOG_JS
        if "seerEmergencySwitch" not in render._EMERGENCY_ACTION_TYPES:
            render._EMERGENCY_ACTION_TYPES = (
                *render._EMERGENCY_ACTION_TYPES,
                "seerEmergencySwitch",
            )
        if "seerPathNav" not in render._DRIVE_ACTION_TYPES:
            render._DRIVE_ACTION_TYPES = (*render._DRIVE_ACTION_TYPES, "seerPathNav")
        if "seerSetDO" not in render._DRIVE_ACTION_TYPES:
            # The full API 1013/6001 interface lives on /io; keep the raw
            # instant action out of the generic Vehicle actions list.
            render._DRIVE_ACTION_TYPES = (*render._DRIVE_ACTION_TYPES, "seerSetDO")
        # An emergency switch must remain one-click. Remove a stale entry as
        # well, so reloading this drop-in in a long-running test process cannot
        # retain the previous confirmation gate.
        server._CONFIRM_REQUIRED_ACTIONS = tuple(
            action_type
            for action_type in server._CONFIRM_REQUIRED_ACTIONS
            if action_type != "seerEmergencySwitch"
        )
        required_motion = (
            "seerPathNav",
            "seerCoordinateNav",
            "seerTranslate",
            "seerTurn",
            "seerJackLoad",
            "seerJackUnload",
        )
        server._CONFIRM_REQUIRED_ACTIONS = tuple(
            dict.fromkeys((*server._CONFIRM_REQUIRED_ACTIONS, *required_motion))
        )
        render._seer_dropin_operator_ui_installed = True

    @staticmethod
    def _register_map_view(spec, config, cache_path: Path) -> None:
        _SEER_MAP_VIEWS[spec.key] = {
            "cache_path": Path(cache_path),
            "active_route_path": Path(cache_path).with_name("seer-active-route.json"),
            "block_runtime_path": Path(cache_path).with_name("seer-block-runtime.json"),
            "controller_status_path": Path(cache_path).with_name("seer-controller-status.json"),
            "jack_status_path": Path(cache_path).with_name("seer-jack-status.json"),
            "position_unit": str(config.factsheet.coordinate_unit_position),
            "orientation_unit": str(config.factsheet.coordinate_unit_orientation),
        }

    @staticmethod
    def _register_io_view(spec, cache_path: Path) -> None:
        _SEER_IO_VIEWS[spec.key] = Path(cache_path)

    def build_webui(self):
        from config.config import get_config  # pyright: ignore[reportMissingImports]
        from core import configio, ipc_paths  # pyright: ignore[reportMissingImports]
        from web.credentials import WebUiCredentials  # pyright: ignore[reportMissingImports]
        from web.server import WebUi  # pyright: ignore[reportMissingImports]

        self._patch_seer_title()
        self._patch_seer_operator_ui()
        ipc_paths.RUNTIME_ROOT = self.paths.ipc_root
        config = get_config(
            config_path=self.paths.config_path,
            extensions_path=self.paths.extensions_path,
            recipes_path=self.paths.recipes_path,
        )
        config.vehicle.manufacturer = "seer"
        config.vehicle.serial_number = self.serial
        config.vehicle.vehicle_ip = self.vehicle_ip or "127.0.0.1"
        config.vehicle.vehicle_port = self.state_port
        config.factsheet.series_name = "SEER"
        config.web_ui.enabled = True
        config.web_ui.host = self.host
        config.web_ui.port = self.requested_port

        spec = self._spec(config)
        _require_motion_confirmation((spec,))
        self._register_map_view(
            spec, config, self.paths.runtime_dir / "seer-map.json"
        )
        self._register_io_view(
            spec, self.paths.runtime_dir / "seer-io.json"
        )
        monitor = _seer_file_monitor(self.serial)
        trace = _build_vda_trace(config, self.paths.runtime_dir)
        self.vda_traces = {spec.key: trace}
        from .dropin_control import TcpControlSender

        sender = Vda5050WebSender(
            trace,
            TcpControlSender(self.control_host, self.control_ipc_port),
            mode=self.webui_vda_transport,
        )
        self.web = WebUi(
            specs=[spec],
            controllers={spec.key: self.controller},
            monitors={spec.key: monitor},
            senders={spec.key: sender},
            credentials=WebUiCredentials(self.username, self.password),
            host=self.host,
            port=self.requested_port,
            py=sys.executable,
            config_path=self.paths.config_path,
            validate_config=lambda: configio.validate_on_disk(self.paths.config_path),
            video_url="",
            camera_controller=None,
            camera_config=config.video,
            host_controller=None,
            urobot_controller=None,
            web_cfg=config.web_ui,
            robots_path=self.paths.robots_path,
            startup_error="",
        )
        _suppress_client_disconnect_tracebacks(self.web)
        self.web._seer_vda_traces = self.vda_traces
        self.web._seer_vda_senders = {spec.key: sender}
        self.web._seer_member_bindings = {
            spec.key: {
                "serial": self.serial,
                "vehicle_ip": self.vehicle_ip,
                "control_ipc_port": self.control_ipc_port,
                "runtime_dir": str(self.paths.runtime_dir),
                "map_cache_path": str(self.paths.runtime_dir / "seer-map.json"),
            }
        }
        _bind_seer_hcl_sources(self.web, self.paths)

        def validate_recipe(candidate: Path) -> Tuple[bool, str]:
            try:
                get_config(
                    config_path=self.paths.config_path,
                    extensions_path=self.paths.extensions_path,
                    recipes_path=candidate,
                )
                return True, "SEER Recipe valid"
            except Exception as exc:  # noqa: BLE001 - operator-facing validation
                return False, f"{type(exc).__name__}: {exc}"

        def apply_recipe() -> Tuple[bool, str]:
            try:
                next_config = get_config(
                    config_path=self.paths.config_path,
                    extensions_path=self.paths.extensions_path,
                    recipes_path=self.paths.recipes_path,
                )
                next_config.vehicle.manufacturer = "seer"
                next_config.vehicle.serial_number = self.serial
                next_config.vehicle.vehicle_ip = self.vehicle_ip or "127.0.0.1"
                next_config.vehicle.vehicle_port = self.state_port
                next_config.factsheet.series_name = "SEER"
                next_spec = self._spec(next_config)
                self.web._specs[next_spec.key] = next_spec
                _require_motion_confirmation((next_spec,))
                ok, message = self.controller.restart()
                return bool(ok), message
            except Exception as exc:  # noqa: BLE001 - operator-facing apply result
                return False, f"{type(exc).__name__}: {exc}"

        _bind_seer_recipe_builder(
            self.web,
            validate_recipe=validate_recipe,
            apply_recipe=apply_recipe,
        )
        self.web._seer_recipe_paths = {spec.key: self.paths.recipes_path}
        self.web._seer_recipe_validators = {spec.key: validate_recipe}
        self.web._seer_recipe_appliers = {spec.key: apply_recipe}
        _SEER_MAP_VIEWS[spec.key]["csrf"] = self.web._csrf
        return self.web

    @property
    def port(self) -> int:
        if self.web is None:
            return self.requested_port
        return self.web.port

    @staticmethod
    def _install_shutdown_handlers(stop_requested: threading.Event):
        """Translate Ctrl+C/SIGTERM into one graceful shutdown request.

        Windows may defer ``KeyboardInterrupt`` while the main thread is in an
        unbounded ``Thread.join``.  A short event wait below gives Python a
        regular chance to run these signal handlers.  Signal registration is
        skipped when ``serve`` is called outside the main thread (mainly tests).
        """

        previous = {}
        if threading.current_thread() is not threading.main_thread():
            return lambda: None

        def request_stop(signum, _frame) -> None:
            if not stop_requested.is_set():
                print(f"\nStopping SEER WebUI (signal {signum}) ...", flush=True)
            stop_requested.set()

        # SIGBREAK is included for Windows terminals and for automated
        # process-group shutdown checks.  A normal Ctrl+C arrives as SIGINT.
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)

        def restore() -> None:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

        return restore

    @staticmethod
    def _wait_for_stop(web, stop_requested: threading.Event) -> None:
        """Wait responsively instead of calling the original unbounded join."""

        server_thread = getattr(web, "_thread", None)
        if server_thread is None:
            return
        while server_thread.is_alive() and not stop_requested.wait(0.2):
            pass

    def serve(self) -> int:
        web = self.build_webui()
        stop_requested = threading.Event()
        restore_handlers = self._install_shutdown_handlers(stop_requested)
        web_started = False
        try:
            for trace in self.vda_traces.values():
                trace.start()
            if self.auto_start:
                ok, message = self.controller.start()
                print(message)
                if not ok:
                    print("WebUI will remain available so the adapter can be started again.")
            if not stop_requested.is_set():
                web.start()
                web_started = True
                print(f"SEER WebUI: http://{self.host}:{web.port}/")
                print(f"Runtime config: {self.paths.config_path}")
                print(f"Adapter log: {self.paths.log_path}")
                print("Press Ctrl+C once to stop WebUI and Adapter.")
                self._wait_for_stop(web, stop_requested)
        except KeyboardInterrupt:
            # Fallback for an interpreter/platform that did not accept the
            # explicit signal handler above.
            print("\nStopping SEER WebUI ...", flush=True)
            stop_requested.set()
        finally:
            try:
                if web_started:
                    web.stop()
            finally:
                try:
                    _ok, message = self.controller.stop()
                    print(message)
                finally:
                    try:
                        for trace in self.vda_traces.values():
                            trace.stop()
                    finally:
                        restore_handlers()
        print("SEER WebUI stopped.")
        return 0


def _write_fleet_manifest(members: Sequence[SeerWebUiApplication]) -> Path:
    """Write the WebUI's generated robots file without touching the original."""

    if not members:
        raise ValueError("at least one SEER member is required")
    path = members[0].paths.robots_path
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Managed by seer_client/run_webui.py; original robots.toml is untouched.",
    ]
    for member in members:
        lines.extend(
            (
                "",
                "[[robot]]",
                f"id = {_toml_string(member.serial)}",
                f"simulator = {str(member.simulator).lower()}",
                f"vehicle_ip = {_toml_string(member.vehicle_ip)}",
                f"x = {member.x}",
                f"y = {member.y}",
                f"theta = {member.theta}",
                f"battery = {member.battery}",
                f"charging = {str(member.charging).lower()}",
                f"state_port = {member.state_port}",
                f"control_port = {member.control_port}",
                f"task_port = {member.task_port}",
                f"config_port = {member.config_port}",
                f"other_port = {member.other_port}",
                f"motor_names = {_toml_string(member.motor_names)}",
                f"auto_start = {str(member.auto_start).lower()}",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class SeerFleetWebUiApplication:
    """One unchanged repository WebUI managing multiple SEER AMRs."""

    def __init__(
        self,
        *,
        robots: Sequence[SeerRobotConfig],
        username: str,
        password: str,
        host: str = "127.0.0.1",
        port: int = 9010,
        mqtt_host: Optional[str] = None,
        mqtt_port: Optional[int] = None,
        auto_start: bool = True,
        webui_vda_transport: str = "mqtt",
        runtime_root: Optional[Path] = None,
        ipc_root: Optional[Path] = None,
        fleet_path: Optional[Path] = None,
    ) -> None:
        self.robots = tuple(
            robot.with_global_mqtt(mqtt_host, mqtt_port)
            for robot in validate_seer_fleet(robots)
        )
        self.username = username.strip()
        self.password = password
        self.host = str(host)
        self.requested_port = int(port)
        self.webui_vda_transport = str(webui_vda_transport or "mqtt").strip().lower()
        if self.webui_vda_transport not in {"local", "mqtt"}:
            raise ValueError("webui_vda_transport must be 'local' or 'mqtt'")
        self.runtime_root = (
            Path(runtime_root)
            if runtime_root is not None
            else SEER_CLIENT_ROOT / "runtime"
        )
        self.ipc_root = (
            Path(ipc_root)
            if ipc_root is not None
            else self.runtime_root / "ipc"
        )
        self.nickname_path = self.runtime_root / "amr-nicknames.json"
        self.nicknames = _load_amr_nicknames(self.nickname_path)
        from .dropin_control import reserve_local_ports

        self.members = []
        fleet_control_ports = reserve_local_ports(len(self.robots))
        for robot, control_ipc_port in zip(self.robots, fleet_control_ports):
            paths = DropInPaths.for_robot(
                robot.serial,
                runtime_root=self.runtime_root,
                ipc_root=self.ipc_root,
            )
            self.members.append(
                SeerWebUiApplication(
                    serial=robot.serial,
                    simulator=robot.simulator,
                    vehicle_ip=robot.vehicle_ip,
                    username=self.username,
                    password=self.password,
                    host=self.host,
                    port=self.requested_port,
                    mqtt_host=robot.mqtt_host,
                    mqtt_port=robot.mqtt_port,
                    x=robot.x,
                    y=robot.y,
                    theta=robot.theta,
                    battery=robot.battery,
                    charging=robot.charging,
                    state_port=robot.state_port,
                    control_port=robot.control_port,
                    task_port=robot.task_port,
                    config_port=robot.config_port,
                    other_port=robot.other_port,
                    motor_names=robot.motor_names,
                    auto_start=bool(auto_start and robot.auto_start),
                    webui_vda_transport=self.webui_vda_transport,
                    paths=paths,
                    control_ipc_port=control_ipc_port,
                )
            )
        self.robots_path = (
            Path(fleet_path).resolve()
            if fleet_path is not None
            else _write_fleet_manifest(self.members)
        )
        self.web = None
        self.specs = []
        self.controllers = {}
        self.monitors = {}
        self.senders = {}
        self.vda_traces = {}

    def _validate_configs(self):
        from config.config import get_config  # pyright: ignore[reportMissingImports]

        for member in self.members:
            try:
                get_config(
                    config_path=member.paths.config_path,
                    extensions_path=member.paths.extensions_path,
                    recipes_path=member.paths.recipes_path,
                )
            except Exception as exc:  # noqa: BLE001 - operator-facing validation
                return False, f"{member.serial}: {type(exc).__name__}: {exc}"
        return True, f"{len(self.members)} SEER configs valid"

    def _validate_robots(self):
        try:
            load_seer_fleet(self.robots_path)
            return True, "SEER fleet valid; restart WebUI to apply changes"
        except SeerFleetError as exc:
            return False, str(exc)

    def build_webui(self):
        from config.config import get_config  # pyright: ignore[reportMissingImports]
        from core import ipc_paths  # pyright: ignore[reportMissingImports]
        from web.credentials import WebUiCredentials  # pyright: ignore[reportMissingImports]
        from web.server import WebUi  # pyright: ignore[reportMissingImports]

        SeerWebUiApplication._patch_seer_title()
        SeerWebUiApplication._patch_seer_operator_ui()
        ipc_paths.RUNTIME_ROOT = self.ipc_root
        configs = []
        specs = []
        controllers = {}
        monitors = {}
        senders = {}
        traces = {}
        for robot, member in zip(self.robots, self.members):
            config = get_config(
                config_path=member.paths.config_path,
                extensions_path=member.paths.extensions_path,
                recipes_path=member.paths.recipes_path,
            )
            config.vehicle.manufacturer = "seer"
            config.vehicle.serial_number = member.serial
            config.vehicle.vehicle_ip = member.vehicle_ip or "127.0.0.1"
            config.vehicle.vehicle_port = member.state_port
            config.factsheet.series_name = "SEER"
            config.web_ui.enabled = True
            config.web_ui.host = self.host
            config.web_ui.port = self.requested_port
            display_name = (
                self.nicknames.get(member.vehicle_ip)
                or f"SEER {member.serial}"
            )
            spec = member._spec(
                config,
                key=robot.key,
                display_name=display_name,
            )
            member._register_map_view(
                spec, config, member.paths.runtime_dir / "seer-map.json"
            )
            member._register_io_view(
                spec, member.paths.runtime_dir / "seer-io.json"
            )
            configs.append(config)
            specs.append(spec)
            controllers[spec.key] = member.controller
            monitors[spec.key] = _seer_file_monitor(member.serial)
            trace = _build_vda_trace(config, member.paths.runtime_dir)
            traces[spec.key] = trace
            from .dropin_control import TcpControlSender

            senders[spec.key] = Vda5050WebSender(
                trace,
                TcpControlSender(member.control_host, member.control_ipc_port),
                mode=self.webui_vda_transport,
            )

        base_config = configs[0]
        self.specs = specs
        self.controllers = controllers
        self.monitors = monitors
        self.senders = senders
        self.vda_traces = traces
        _require_motion_confirmation(specs)
        self.web = WebUi(
            specs=specs,
            controllers=controllers,
            monitors=monitors,
            senders=senders,
            credentials=WebUiCredentials(self.username, self.password),
            host=self.host,
            port=self.requested_port,
            py=sys.executable,
            config_path=self.members[0].paths.config_path,
            validate_config=self._validate_configs,
            video_url="",
            camera_controller=None,
            camera_config=base_config.video,
            host_controller=None,
            urobot_controller=None,
            web_cfg=base_config.web_ui,
            robots_path=self.robots_path,
            validate_robots=self._validate_robots,
            startup_error="",
        )
        _suppress_client_disconnect_tracebacks(self.web)
        self.web._seer_vda_traces = self.vda_traces
        self.web._seer_vda_senders = self.senders
        self.web._seer_member_bindings = {
            spec.key: {
                "serial": member.serial,
                "vehicle_ip": member.vehicle_ip,
                "control_ipc_port": member.control_ipc_port,
                "runtime_dir": str(member.paths.runtime_dir),
                "map_cache_path": str(member.paths.runtime_dir / "seer-map.json"),
            }
            for spec, member in zip(specs, self.members)
        }
        self.web._seer_nickname_path = self.nickname_path
        self.web._seer_nicknames = dict(self.nicknames)
        _bind_seer_hcl_sources(self.web, self.members[0].paths)

        # Block Builder Recipes are isolated per AMR.  The selected robot key
        # chooses one recipes.hcl, one validator, one spec update, and one
        # Adapter restart.  Other AMRs continue running untouched.
        recipe_paths = {}
        recipe_validators = {}
        recipe_appliers = {}

        for robot, member in zip(self.robots, self.members):
            recipe_paths[robot.key] = member.paths.recipes_path

            def validate_member_recipe(candidate: Path, *, _member=member) -> Tuple[bool, str]:
                try:
                    get_config(
                        config_path=_member.paths.config_path,
                        extensions_path=_member.paths.extensions_path,
                        recipes_path=candidate,
                    )
                    return True, f"{_member.serial} Recipe valid"
                except Exception as exc:  # noqa: BLE001 - operator-facing validation
                    return False, f"{_member.serial}: {type(exc).__name__}: {exc}"

            def apply_member_recipe(*, _robot=robot, _member=member) -> Tuple[bool, str]:
                try:
                    next_config = get_config(
                        config_path=_member.paths.config_path,
                        extensions_path=_member.paths.extensions_path,
                        recipes_path=_member.paths.recipes_path,
                    )
                    next_config.vehicle.manufacturer = "seer"
                    next_config.vehicle.serial_number = _member.serial
                    next_config.vehicle.vehicle_ip = _member.vehicle_ip or "127.0.0.1"
                    next_config.vehicle.vehicle_port = _member.state_port
                    next_config.factsheet.series_name = "SEER"
                    current = self.web._specs.get(_robot.key)
                    display_name = (
                        getattr(current, "display_name", "")
                        or self.nicknames.get(_member.vehicle_ip)
                        or f"SEER {_member.serial}"
                    )
                    next_spec = _member._spec(
                        next_config,
                        key=_robot.key,
                        display_name=display_name,
                    )
                    self.web._specs[_robot.key] = next_spec
                    self.specs = [
                        next_spec if item.key == _robot.key else item
                        for item in self.specs
                    ]
                    _require_motion_confirmation((next_spec,))
                    ok, message = _member.controller.restart()
                    if not ok:
                        return False, f"{_member.serial}: {message}"
                    if not _wait_tcp_ready(
                        _member.control_host, _member.control_ipc_port, timeout=10.0
                    ):
                        return False, (
                            f"{_member.serial}: Adapter는 재시작됐지만 Block Builder 제어 채널 "
                            f"{_member.control_host}:{_member.control_ipc_port} 준비가 완료되지 않았습니다"
                        )
                    return True, (
                        f"{_member.serial} Adapter 재시작 + Block Builder 제어 채널 준비 완료 · {message}"
                    )
                except Exception as exc:  # noqa: BLE001 - operator-facing apply result
                    return False, f"{_member.serial}: {type(exc).__name__}: {exc}"

            recipe_validators[robot.key] = validate_member_recipe
            recipe_appliers[robot.key] = apply_member_recipe

        self.web._seer_recipe_paths = recipe_paths
        self.web._seer_recipe_validators = recipe_validators
        self.web._seer_recipe_appliers = recipe_appliers
        first_key = specs[0].key
        self.web._seer_recipes_path = recipe_paths[first_key]
        _bind_seer_recipe_builder(
            self.web,
            validate_recipe=recipe_validators[first_key],
            apply_recipe=recipe_appliers[first_key],
        )
        for spec in specs:
            _SEER_MAP_VIEWS[spec.key]["csrf"] = self.web._csrf
        return self.web

    @property
    def port(self) -> int:
        if self.web is None:
            return self.requested_port
        return self.web.port

    def serve(self) -> int:
        web = self.build_webui()
        stop_requested = threading.Event()
        restore_handlers = SeerWebUiApplication._install_shutdown_handlers(
            stop_requested
        )
        web_started = False
        try:
            for trace in self.vda_traces.values():
                trace.start()
            for member in self.members:
                if not member.auto_start:
                    continue
                ok, message = member.controller.start()
                print(f"[{member.serial}] {message}")
                if not ok:
                    print(f"[{member.serial}] Adapter can be started again from WebUI.")
            if not stop_requested.is_set():
                web.start()
                web_started = True
                print(f"SEER Fleet WebUI: http://{self.host}:{web.port}/")
                print(
                    "Managed AMRs: "
                    + ", ".join(member.serial for member in self.members)
                )
                print(f"Fleet runtime: {self.runtime_root}")
                print("Press Ctrl+C once to stop WebUI and every Adapter.")
                SeerWebUiApplication._wait_for_stop(web, stop_requested)
        except KeyboardInterrupt:
            print("\nStopping SEER Fleet WebUI ...", flush=True)
            stop_requested.set()
        finally:
            try:
                if web_started:
                    web.stop()
            finally:
                try:
                    for member in reversed(self.members):
                        try:
                            _ok, message = member.controller.stop()
                            print(f"[{member.serial}] {message}")
                        except Exception as exc:  # keep cleaning the other AMRs
                            print(f"[{member.serial}] stop failed: {exc}")
                finally:
                    try:
                        for trace in self.vda_traces.values():
                            trace.stop()
                    finally:
                        restore_handlers()
        print("SEER Fleet WebUI stopped.")
        return 0
