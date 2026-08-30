"""Normalize SEER ``.smap`` JSON and render a dependency-free WebUI SVG."""

from __future__ import annotations

import html
import hashlib
import heapq
import json
import math
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MAP_CACHE_SCHEMA = 3
ACTIVE_ROUTE_SCHEMA = 1
DEFAULT_MAX_NORMAL_POINTS = 12000
POINT_ON_TOLERANCE_M = 0.10


SEER_MAP_INTERACTION_JS = r"""
(function(){
  if(window.__seerMapZoom) return;
  window.__seerMapZoom=true;
  var states=new Map(), drag=null, BASE_W=1000, BASE_H=620, MIN=1, MAX=8,
      MARKER_MIN=.35, MARKER_MAX=1.5, POINT_MIN=.35, POINT_MAX=2,
      LABEL_MIN=.25, LABEL_MAX=1.5;
  function key(svg){return 'seer-map-zoom:'+String(svg.dataset.seerMapKey||'default');}
  function clamp(value,low,high){return Math.max(low,Math.min(high,value));}
  function closestElement(target,selector){
    var node=target;
    while(node&&node!==document){
      if(node.matches&&node.matches(selector)) return node;
      node=node.parentNode;
    }
    return null;
  }
  function pointFromEvent(event){
    var point=closestElement(event.target,'[data-seer-path-target]');
    if(point) return point;
    var svg=closestElement(event.target,'.seer-map-svg');
    if(!svg) return null;
    var candidates=svg.querySelectorAll('[data-seer-path-target]'), nearest=null, best=Infinity;
    candidates.forEach(function(candidate){
      var marker=candidate.querySelector('circle');
      if(!marker) return;
      var rect=marker.getBoundingClientRect();
      var dx=event.clientX-(rect.left+rect.width/2);
      var dy=event.clientY-(rect.top+rect.height/2);
      var distance=Math.sqrt(dx*dx+dy*dy);
      var hitRadius=Math.max(18,Math.max(rect.width,rect.height)/2+10);
      if(distance<=hitRadius&&distance<best){nearest=candidate;best=distance;}
    });
    return nearest;
  }
  function valid(raw){
    return raw&&Number.isFinite(raw.scale)&&Number.isFinite(raw.x)&&Number.isFinite(raw.y);
  }
  function loadStored(k){
    var raw=null, current=null;
    try{raw=localStorage.getItem(k);}catch(e){}
    // Migrate a value saved by an older build in this still-open tab.
    if(raw===null){try{raw=sessionStorage.getItem(k);}catch(e){}}
    try{current=JSON.parse(raw||'null');}catch(e){current=null;}
    return current;
  }
  function saveStored(k,current){
    try{
      localStorage.setItem(k,JSON.stringify(current));
      sessionStorage.removeItem(k);
    }catch(e){
      // Storage can be disabled by the browser; in-memory state still works.
    }
  }
  function state(svg){
    var k=key(svg), current=states.get(k);
    if(current) return current;
    current=loadStored(k);
    if(!valid(current)) current={scale:1,x:0,y:0,markerScale:1,pointScale:1,labelScale:1,routeLayerOrder:'above'};
    current.scale=clamp(current.scale,MIN,MAX);
    if(!Number.isFinite(current.markerScale)) current.markerScale=1;
    current.markerScale=clamp(current.markerScale,MARKER_MIN,MARKER_MAX);
    if(!Number.isFinite(current.pointScale)) current.pointScale=1;
    current.pointScale=clamp(current.pointScale,POINT_MIN,POINT_MAX);
    if(!Number.isFinite(current.labelScale)) current.labelScale=1;
    current.labelScale=clamp(current.labelScale,LABEL_MIN,LABEL_MAX);
    if(current.routeLayerOrder!=='below') current.routeLayerOrder='above';
    states.set(k,current); return current;
  }
  function applyRouteLayerOrder(card,current){
    if(!card) return;
    var baseLayer=card.querySelector('[data-seer-route-base-layer]');
    var highlightLayer=card.querySelector('[data-seer-route-highlight-layer]');
    if(baseLayer&&highlightLayer&&baseLayer.parentNode===highlightLayer.parentNode){
      if(current.routeLayerOrder==='below'){
        if(highlightLayer.nextElementSibling!==baseLayer) baseLayer.parentNode.insertBefore(highlightLayer,baseLayer);
      }else{
        if(baseLayer.nextElementSibling!==highlightLayer) baseLayer.parentNode.insertBefore(highlightLayer,baseLayer.nextSibling);
      }
    }
    var order=card.querySelector('[data-seer-route-layer-order]');
    if(order) order.value=current.routeLayerOrder;
  }
  function apply(svg,current){
    var width=BASE_W/current.scale, height=BASE_H/current.scale;
    current.x=clamp(current.x,0,BASE_W-width);
    current.y=clamp(current.y,0,BASE_H-height);
    svg.setAttribute('viewBox',[current.x,current.y,width,height].join(' '));
    var card=svg.closest('.seer-map-card');
    var label=card&&card.querySelector('.seer-map-zoom-value');
    if(label) label.textContent=Math.round(current.scale*100)+'%';
    if(card){
      applyRouteLayerOrder(card,current);
      var inverseScale=1/current.scale;
      card.querySelectorAll('[data-seer-map-fixed-point]').forEach(function(point){
        var px=parseFloat(point.dataset.seerMapPointX||'0');
        var py=parseFloat(point.dataset.seerMapPointY||'0');
        point.setAttribute('transform','translate('+px.toFixed(2)+' '+py.toFixed(2)+') scale('+inverseScale.toFixed(5)+')');
      });
      card.querySelectorAll('[data-seer-robot-marker]').forEach(function(marker){
        var base=marker.dataset.seerRobotBaseTransform||'';
        marker.setAttribute('transform',(base+' scale('+current.markerScale+')').trim());
      });
      card.querySelectorAll('[data-seer-point-marker]').forEach(function(pointMarker){
        var base=parseFloat(pointMarker.dataset.seerPointBaseRadius||'16');
        pointMarker.setAttribute('r',(base*current.pointScale).toFixed(2));
      });
      card.querySelectorAll('[data-seer-point-label]').forEach(function(pointLabel){
        var base=parseFloat(pointLabel.dataset.seerPointLabelBase||'13');
        pointLabel.style.fontSize=(base*current.labelScale).toFixed(2)+'px';
      });
      var size=card.querySelector('[data-seer-robot-size]');
      var sizeLabel=card.querySelector('.seer-map-robot-size-value');
      var pointMarkerSize=card.querySelector('[data-seer-point-size]');
      var pointMarkerSizeLabel=card.querySelector('.seer-map-point-size-value');
      var pointSize=card.querySelector('[data-seer-point-label-size]');
      var pointSizeLabel=card.querySelector('.seer-map-point-label-size-value');
      if(size) size.value=String(Math.round(current.markerScale*100));
      if(sizeLabel) sizeLabel.textContent=Math.round(current.markerScale*100)+'%';
      if(pointMarkerSize) pointMarkerSize.value=String(Math.round(current.pointScale*100));
      if(pointMarkerSizeLabel) pointMarkerSizeLabel.textContent=Math.round(current.pointScale*100)+'%';
      if(pointSize) pointSize.value=String(Math.round(current.labelScale*100));
      if(pointSizeLabel) pointSizeLabel.textContent=Math.round(current.labelScale*100)+'%';
    }
    saveStored(key(svg),current);
  }
  function applyAll(){
    document.querySelectorAll('.seer-map-svg').forEach(function(svg){apply(svg,state(svg));});
  }
  function zoom(svg,next,rx,ry){
    var current=state(svg), oldW=BASE_W/current.scale, oldH=BASE_H/current.scale;
    rx=clamp(rx == null ? 0.5 : rx,0,1); ry=clamp(ry == null ? 0.5 : ry,0,1);
    var anchorX=current.x+rx*oldW, anchorY=current.y+ry*oldH;
    current.scale=clamp(next,MIN,MAX);
    var newW=BASE_W/current.scale, newH=BASE_H/current.scale;
    current.x=anchorX-rx*newW; current.y=anchorY-ry*newH;
    apply(svg,current);
  }
  function closePathDialog(dialog){
    if(!dialog) return;
    dialog.classList.remove('is-open');
    var card=dialog.closest('.seer-map-card');
    if(card) card.querySelectorAll('[data-seer-route-highlight-layer] .seer-route-preview').forEach(function(edge){
      edge.remove();
    });
    var status=dialog.querySelector('[data-seer-map-nav-status]');
    if(status) status.textContent='';
  }
  function previewRoute(dialog){
    if(!dialog) return;
    var card=dialog.closest('.seer-map-card'), svg=card&&card.querySelector('.seer-map-svg');
    if(!svg) return;
    var highlightLayer=svg.querySelector('[data-seer-route-highlight-layer]');
    if(!highlightLayer) return;
    highlightLayer.querySelectorAll('.seer-route-preview').forEach(function(edge){
      edge.remove();
    });
    var input=dialog.querySelector('input[name="navigation_mode"]:checked');
    var routeJson=input&&input.dataset?input.dataset.seerRoutePoints:'';
    if(!routeJson) return;
    var points;
    try{points=JSON.parse(routeJson);}catch(e){return;}
    if(!Array.isArray(points)) return;
    var combined='';
    for(var index=0;index+1<points.length;index++){
      var edgeKey=String(points[index])+'|'+String(points[index+1]),matched=null;
      svg.querySelectorAll('[data-seer-route-edge]').forEach(function(edge){
        if(!matched&&edge.dataset.seerRouteEdge===edgeKey) matched=edge;
      });
      if(!matched) continue;
      var pathData=String(matched.getAttribute('d')||'');
      if(!pathData) continue;
      if(!combined) combined=pathData;
      else{
        var command=pathData.search(/\s[QC]\s/);
        combined+=' '+(command>=0?pathData.slice(command+1):pathData);
      }
    }
    if(combined){
      var overlay=document.createElementNS('http://www.w3.org/2000/svg','path');
      overlay.setAttribute('d',combined);
      overlay.setAttribute('class','seer-map-route-edge seer-route-preview seer-route-highlight');
      highlightLayer.appendChild(overlay);
    }
  }
  function openPathDialog(point){
    var id=point&&point.dataset.seerDialogId;
    var dialog=id&&document.getElementById(id);
    if(!dialog) return;
    document.querySelectorAll('.seer-map-nav-dialog.is-open').forEach(function(other){
      if(other!==dialog) closePathDialog(other);
    });
    var status=dialog.querySelector('[data-seer-map-nav-status]');
    if(status) status.textContent='';
    dialog.classList.add('is-open');
    previewRoute(dialog);
    var cancel=dialog.querySelector('[data-seer-map-nav-cancel]');
    if(cancel) cancel.focus();
  }
  document.addEventListener('click',function(event){
    var point=pointFromEvent(event);
    if(point){event.preventDefault();event.stopPropagation();openPathDialog(point);return;}
    var cancel=closestElement(event.target,'[data-seer-map-nav-cancel]');
    if(cancel){event.preventDefault();closePathDialog(closestElement(cancel,'.seer-map-nav-dialog'));return;}
    var button=closestElement(event.target,'[data-seer-map-zoom]');
    if(!button) return;
    var card=button.closest('.seer-map-card'), svg=card&&card.querySelector('.seer-map-svg');
    if(!svg) return;
    var action=button.dataset.seerMapZoom, current=state(svg);
    if(action==='in') zoom(svg,current.scale*1.25,.5,.5);
    else if(action==='out') zoom(svg,current.scale/1.25,.5,.5);
    else if(action==='fit'){current.scale=1;current.x=0;current.y=0;apply(svg,current);}
  });
  document.addEventListener('keydown',function(event){
    if(event.key!=='Escape') return;
    var dialog=document.querySelector('.seer-map-nav-dialog.is-open');
    if(dialog){event.preventDefault();closePathDialog(dialog);}
  });
  document.addEventListener('input',function(event){
    var input=closestElement(event.target,'[data-seer-robot-size],[data-seer-point-size],[data-seer-point-label-size]');
    if(!input) return;
    var card=input.closest('.seer-map-card'), svg=card&&card.querySelector('.seer-map-svg');
    if(!svg) return;
    var current=state(svg), percent=parseFloat(input.value);
    if(Number.isFinite(percent)){
      if(input.matches('[data-seer-point-label-size]')) current.labelScale=clamp(percent/100,LABEL_MIN,LABEL_MAX);
      else if(input.matches('[data-seer-point-size]')) current.pointScale=clamp(percent/100,POINT_MIN,POINT_MAX);
      else current.markerScale=clamp(percent/100,MARKER_MIN,MARKER_MAX);
    }
    apply(svg,current);
  });
  document.addEventListener('change',function(event){
    var order=closestElement(event.target,'[data-seer-route-layer-order]');
    if(order){
      var card=order.closest('.seer-map-card'), svg=card&&card.querySelector('.seer-map-svg');
      if(svg){var current=state(svg);current.routeLayerOrder=order.value==='below'?'below':'above';apply(svg,current);}
      return;
    }
    var option=closestElement(event.target,'input[name="navigation_mode"]');
    if(option) previewRoute(option.closest('.seer-map-nav-dialog'));
  });
  document.addEventListener('submit',function(event){
    var form=closestElement(event.target,'[data-seer-map-nav-form]');
    if(!form) return;
    event.preventDefault();
    var dialog=form.closest('.seer-map-nav-dialog');
    var status=dialog&&dialog.querySelector('[data-seer-map-nav-status]');
    var choice=form.querySelector('input[name="navigation_mode"]:checked');
    var routeField=form.querySelector('input[type="hidden"][name="route_points"]');
    var sourceField=form.querySelector('input[type="hidden"][name="source_id"]');
    if(!choice){if(status)status.textContent='이동 방식을 선택하세요.';return;}
    var selectedRoute=(choice.dataset&&choice.dataset.seerRoutePoints)||'';
    // A designated route carries the complete FMS-style node sequence.
    // The target-only option omits route_points and becomes a one-node order.
    if(routeField){
      routeField.value=selectedRoute;
      routeField.disabled=!selectedRoute;
    }
    // Only a designated multi-node order needs an explicit source node.
    if(sourceField) sourceField.value=selectedRoute?(choice.dataset.seerRouteSource||''):'';
    var entryFields={reentry_id:'seerEntryId',reentry_x:'seerEntryX',reentry_y:'seerEntryY',reentry_theta:'seerEntryTheta'};
    Object.keys(entryFields).forEach(function(name){
      var field=form.querySelector('input[type="hidden"][name="'+name+'"]');
      if(field) field.value=(choice.dataset&&choice.dataset[entryFields[name]])||'';
    });
    var controls=form.querySelectorAll('button,a[data-seer-map-nav-cancel]');
    controls.forEach(function(control){control.setAttribute('aria-disabled','true');});
    form.querySelectorAll('button').forEach(function(button){button.disabled=true;});
    var isFree=choice.value==='free', isReentry=choice.value==='reentry';
    var modeLabel=(choice.dataset&&choice.dataset.seerNavLabel)|| (isFree?'Free Nav':(isReentry?'경로 재진입':'VDA5050 Order'));
    if(status) status.textContent=modeLabel+' 명령을 전송 중입니다...';
    var body=new URLSearchParams();
    new FormData(form).forEach(function(value,name){body.append(name,String(value));});
    fetch(form.action,{method:'POST',credentials:'same-origin',redirect:'follow',cache:'no-store',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()})
      .then(function(response){
        if(!response.ok)throw new Error('HTTP '+response.status);
        return response.text().then(function(html){return {response:response,html:html};});
      })
      .then(function(result){
        var finalUrl=null;
        try{finalUrl=new URL(result.response.url,window.location.href);}catch(_error){}
        var errorMessage=finalUrl&&finalUrl.searchParams.get('err');
        if(!errorMessage&&result.html){
          var nextDocument=new DOMParser().parseFromString(result.html,'text/html');
          var notice=nextDocument.querySelector('.error-notice');
          if(notice) errorMessage=String(notice.textContent||'').trim();
        }
        if(errorMessage) throw new Error(errorMessage);
        if(status) status.textContent=modeLabel+' VDA5050 MQTT 발행을 요청했습니다. SEER 작업 상태를 확인합니다...';
        window.setTimeout(function(){
          closePathDialog(dialog);
          if(window.__amrPollTick) window.__amrPollTick();
        },700);
      })
      .catch(function(error){
        if(status) status.textContent='이동 명령 전달 실패 · '+String(error.message||error);
      })
      .finally(function(){
        controls.forEach(function(control){control.removeAttribute('aria-disabled');});
        form.querySelectorAll('button').forEach(function(button){button.disabled=false;});
      });
  });
  document.addEventListener('wheel',function(event){
    var svg=closestElement(event.target,'.seer-map-svg');
    if(!svg||drag) return;
    event.preventDefault();
    var rect=svg.getBoundingClientRect();
    var rx=(event.clientX-rect.left)/Math.max(rect.width,1);
    var ry=(event.clientY-rect.top)/Math.max(rect.height,1);
    var factor=event.deltaY<0?1.2:(1/1.2);
    zoom(svg,state(svg).scale*factor,rx,ry);
  },{passive:false});
  document.addEventListener('pointerdown',function(event){
    var svg=closestElement(event.target,'.seer-map-svg');
    if(!svg||event.button!==0) return;
    if(closestElement(event.target,'[data-seer-path-target]')) return;
    var current=state(svg);
    apply(svg,current);
    drag={svg:svg,id:event.pointerId,clientX:event.clientX,clientY:event.clientY,
          x:current.x,y:current.y,width:BASE_W/current.scale,height:BASE_H/current.scale};
    window.__seerMapDragging=true;
    svg.classList.add('seer-map-dragging');
    try{svg.setPointerCapture(event.pointerId);}catch(e){}
    event.preventDefault();
  });
  document.addEventListener('pointermove',function(event){
    if(!drag||event.pointerId!==drag.id) return;
    var rect=drag.svg.getBoundingClientRect(), current=state(drag.svg);
    current.x=drag.x-(event.clientX-drag.clientX)*drag.width/Math.max(rect.width,1);
    current.y=drag.y-(event.clientY-drag.clientY)*drag.height/Math.max(rect.height,1);
    apply(drag.svg,current); event.preventDefault();
  });
  function endDrag(event){
    if(!drag||(event&&event.pointerId!=null&&event.pointerId!==drag.id)) return;
    var svg=drag.svg, pointerId=drag.id;
    drag=null; window.__seerMapDragging=false;
    svg.classList.remove('seer-map-dragging');
    try{if(svg.hasPointerCapture(pointerId))svg.releasePointerCapture(pointerId);}catch(e){}
  }
  document.addEventListener('pointerup',endDrag);
  document.addEventListener('pointercancel',endDrag);
  document.addEventListener('lostpointercapture',endDrag);
  window.addEventListener('blur',function(){endDrag(null);});
  document.addEventListener('visibilitychange',function(){if(document.hidden)endDrag(null);});
  var content=document.getElementById('content');
  if(content) new MutationObserver(function(){requestAnimationFrame(applyAll);})
    .observe(content,{childList:true,subtree:true});
  requestAnimationFrame(applyAll);
})();
"""


_SEER_MAP_NAV_STYLE = r"""
<style>
.seer-map-point[data-seer-path-target]{cursor:pointer;outline:none}
.seer-map-point[data-seer-path-target]:hover circle,
.seer-map-point[data-seer-path-target]:focus circle{stroke:#fff;stroke-width:3}
.seer-map-nav-dialog{display:none;position:fixed;inset:0;z-index:1000;
  place-items:center;padding:16px;background:rgba(0,0,0,.62)}
.seer-map-nav-dialog:target,.seer-map-nav-dialog.is-open{display:grid}
.seer-map-nav-modal{width:min(620px,calc(100% - 32px));max-height:calc(100vh - 48px);overflow:auto;border:1px solid #557086;
  border-radius:14px;background:#10212d;color:#eaf6ff;padding:22px;box-shadow:0 18px 55px rgba(0,0,0,.45)}
.seer-map-nav-dialog h3{margin:0 0 10px}.seer-map-nav-dialog p{margin:0 0 18px}
.seer-map-nav-status:empty{display:none}.seer-map-nav-status{color:#9bdcf0}
.seer-map-nav-actions{display:flex;justify-content:flex-end;align-items:center;gap:9px}
.seer-map-nav-actions form{width:100%;margin:0}
.seer-map-route-options{display:grid;gap:9px;margin:0 0 18px}
.seer-map-route-option{display:grid;grid-template-columns:auto minmax(0,1fr);gap:10px;align-items:start;
  padding:11px;border:1px solid #38556b;border-radius:10px;background:#0a1924;cursor:pointer}
.seer-map-route-option:has(input:checked){border-color:#ffd166;background:#26301f}
.seer-map-free-option{border-color:#3876a3}
.seer-map-free-option:has(input:checked){border-color:#54c7ff;background:#102d3d}
.seer-map-route-option input{margin-top:4px}.seer-map-route-option strong{display:block}
.seer-map-route-option small{display:block;margin-top:4px;color:#b8cad6;overflow-wrap:anywhere}
.seer-map-waypoint-delay{display:grid;gap:5px;margin:0 0 18px;color:#e9f2f8;font-weight:700}
.seer-map-waypoint-delay input{width:150px;max-width:100%;padding:8px 10px;border:1px solid #4b687a;border-radius:8px;background:#07141e;color:#f4f8fb}
.seer-map-waypoint-delay small{color:#b8cad6;font-weight:400;line-height:1.45}
.seer-map-nav-buttons{display:flex;justify-content:flex-end;align-items:center;gap:9px}
.seer-map-route-edge{transition:stroke .12s ease,stroke-width .12s ease,filter .12s ease;
  stroke-linecap:round;stroke-linejoin:round}
.seer-map-route-edge.seer-route-preview,
.seer-map-route-edge.seer-route-active{stroke:#ffd166!important;stroke-width:8!important;filter:drop-shadow(0 0 4px #ffd166)}
.seer-route-highlight{pointer-events:none}
.seer-map-robot-size,.seer-map-point-size,.seer-map-point-label-size,.seer-map-route-layer-order{display:flex;align-items:center;gap:5px;white-space:nowrap;font-size:.88rem}
.seer-map-robot-size input,.seer-map-point-size input,.seer-map-point-label-size input{width:86px;accent-color:#3ce6a4}
.seer-map-point-size input{accent-color:#ffd166}
.seer-map-route-layer-order select{padding:4px 6px;border:1px solid #557086;border-radius:6px;background:#0a1924;color:#eaf6ff}
</style>
"""


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _xy(value: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(value, Mapping):
        return None
    x, y = _finite(value.get("x")), _finite(value.get("y"))
    return None if x is None or y is None else (x, y)


def _sample(items: Sequence[Any], maximum: int) -> Iterable[Any]:
    if len(items) <= maximum:
        return items
    step = len(items) / float(maximum)
    return (items[min(len(items) - 1, int(index * step))] for index in range(maximum))


def _bezier_length(
    start: Sequence[Any],
    control1: Sequence[Any],
    control2: Sequence[Any],
    end: Sequence[Any],
    *,
    steps: int = 24,
) -> float:
    """Approximate a cubic Bezier length in SEER map metres."""

    p0 = (float(start[0]), float(start[1]))
    p1 = (float(control1[0]), float(control1[1]))
    p2 = (float(control2[0]), float(control2[1]))
    p3 = (float(end[0]), float(end[1]))
    previous = p0
    length = 0.0
    for index in range(1, max(2, int(steps)) + 1):
        t = index / float(max(2, int(steps)))
        inverse = 1.0 - t
        point = (
            inverse**3 * p0[0]
            + 3.0 * inverse**2 * t * p1[0]
            + 3.0 * inverse * t**2 * p2[0]
            + t**3 * p3[0],
            inverse**3 * p0[1]
            + 3.0 * inverse**2 * t * p1[1]
            + 3.0 * inverse * t**2 * p2[1]
            + t**3 * p3[1],
        )
        length += math.hypot(point[0] - previous[0], point[1] - previous[1])
        previous = point
    return length


def _quadratic_bezier_length(
    start: Sequence[Any],
    control: Sequence[Any],
    end: Sequence[Any],
    *,
    steps: int = 16,
) -> float:
    p0 = (float(start[0]), float(start[1]))
    p1 = (float(control[0]), float(control[1]))
    p2 = (float(end[0]), float(end[1]))
    previous = p0
    length = 0.0
    for index in range(1, max(2, int(steps)) + 1):
        t = index / float(max(2, int(steps)))
        inverse = 1.0 - t
        point = (
            inverse**2 * p0[0] + 2.0 * inverse * t * p1[0] + t**2 * p2[0],
            inverse**2 * p0[1] + 2.0 * inverse * t * p1[1] + t**2 * p2[1],
        )
        length += math.hypot(point[0] - previous[0], point[1] - previous[1])
        previous = point
    return length


def _curve_length(
    class_name: Any,
    start: Sequence[Any],
    control1: Sequence[Any],
    control2: Sequence[Any],
    end: Sequence[Any],
) -> float:
    """Match RoboShop's two-segment rendering for ``DegenerateBezier``.

    SEER's newer ``DegenerateBezier`` map entries use two quadratic segments
    joined at the midpoint of ``controlPos1`` and ``controlPos2``.  Treating
    those positions as the two handles of one SVG cubic flattens the displayed
    curve.  Legacy ``BezierPath`` entries remain ordinary cubic Beziers.
    """

    if str(class_name or "").strip().lower() == "degeneratebezier":
        midpoint = (
            (float(control1[0]) + float(control2[0])) / 2.0,
            (float(control1[1]) + float(control2[1])) / 2.0,
        )
        return _quadratic_bezier_length(
            start, control1, midpoint
        ) + _quadratic_bezier_length(midpoint, control2, end)
    return _bezier_length(start, control1, control2, end)


def find_route_options(
    model: Mapping[str, Any],
    source_name: str,
    target_name: str,
    *,
    max_routes: int = 5,
) -> List[Dict[str, Any]]:
    """Return the shortest loop-free SEER map routes using directed curves.

    ``advancedCurveList`` is the controller's path graph: each curve names its
    start and end point and carries metre-based Bezier geometry.  Yen's
    algorithm is used here so a WebUI click can offer several real graph paths
    without enumerating every possible simple path on a large map.
    """

    source = str(source_name or "").strip()
    target = str(target_name or "").strip()
    limit = max(1, min(10, int(max_routes)))
    if not source or not target:
        return []
    if source == target:
        return [{"points": [source], "distance_m": 0.0}]

    edge_lengths: Dict[Tuple[str, str], float] = {}
    curves = model.get("curves")
    if isinstance(curves, list):
        for item in curves:
            if not isinstance(item, Mapping):
                continue
            start = str(item.get("start_name", "") or "").strip()
            end = str(item.get("end_name", "") or "").strip()
            length = _finite(item.get("length_m"))
            if length is None:
                try:
                    length = _curve_length(
                        item.get("class"),
                        item["start"],
                        item["c1"],
                        item["c2"],
                        item["end"],
                    )
                except (KeyError, TypeError, ValueError):
                    length = None
            if not start or not end or start == end or length is None or length <= 0:
                continue
            key = (start, end)
            edge_lengths[key] = min(length, edge_lengths.get(key, length))
    if not edge_lengths:
        return []

    graph: Dict[str, List[Tuple[str, float]]] = {}
    for (start, end), length in edge_lengths.items():
        graph.setdefault(start, []).append((end, length))
    for edges in graph.values():
        edges.sort(key=lambda edge: (edge[1], edge[0]))

    def shortest_path(
        start: str,
        *,
        banned_nodes: Iterable[str] = (),
        banned_edges: Iterable[Tuple[str, str]] = (),
    ) -> Optional[Tuple[List[str], float]]:
        denied_nodes = set(banned_nodes)
        denied_edges = set(banned_edges)
        if start in denied_nodes or target in denied_nodes:
            return None
        queue: List[Tuple[float, Tuple[str, ...]]] = [(0.0, (start,))]
        best: Dict[str, float] = {}
        while queue:
            distance, path = heapq.heappop(queue)
            node = path[-1]
            if distance > best.get(node, float("inf")) + 1e-12:
                continue
            best[node] = distance
            if node == target:
                return list(path), distance
            for next_node, edge_length in graph.get(node, ()):
                if next_node in denied_nodes or (node, next_node) in denied_edges:
                    continue
                next_distance = distance + edge_length
                if next_distance + 1e-12 >= best.get(next_node, float("inf")):
                    continue
                heapq.heappush(queue, (next_distance, path + (next_node,)))
        return None

    first = shortest_path(source)
    if first is None:
        return []
    accepted: List[Tuple[List[str], float]] = [first]
    candidate_heap: List[Tuple[float, Tuple[str, ...]]] = []
    candidate_paths = set()
    while len(accepted) < limit:
        previous_path = accepted[-1][0]
        for index in range(len(previous_path) - 1):
            root = previous_path[: index + 1]
            root_distance = sum(
                edge_lengths[(root[pos], root[pos + 1])]
                for pos in range(len(root) - 1)
            )
            removed_edges = {
                (path[index], path[index + 1])
                for path, _distance in accepted
                if len(path) > index + 1 and path[: index + 1] == root
            }
            spur = shortest_path(
                root[-1], banned_nodes=root[:-1], banned_edges=removed_edges
            )
            if spur is None:
                continue
            path = tuple(root[:-1] + spur[0])
            if path in candidate_paths or any(path == tuple(item[0]) for item in accepted):
                continue
            candidate_paths.add(path)
            heapq.heappush(candidate_heap, (root_distance + spur[1], path))
        if not candidate_heap:
            break
        distance, path = heapq.heappop(candidate_heap)
        candidate_paths.discard(path)
        accepted.append((list(path), distance))

    return [
        {"points": path, "distance_m": distance}
        for path, distance in accepted
    ]



def find_reentry_options(
    model: Mapping[str, Any],
    x: float,
    y: float,
    target_name: str,
    *,
    preferred_source: str = "",
    max_entries: int = 3,
) -> List[Dict[str, Any]]:
    """Find graph-entry points for an AMR that is currently off the path graph.

    RoboShop can recover an off-path robot by first Free-Navigating to a named
    PathPoint and then continuing on the directed graph.  The WebUI mirrors that
    behavior here.  A controller-reported ``current_station`` is preferred when
    it is a valid graph entry; otherwise the shortest reachable entry is used.
    """

    target = str(target_name or "").strip()
    preferred = str(preferred_source or "").strip()
    if not target:
        return []
    candidates: List[Dict[str, Any]] = []
    points = model.get("advanced_points")
    if not isinstance(points, list):
        return []
    for item in points:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "") or "").strip()
        px, py = _finite(item.get("x")), _finite(item.get("y"))
        if not name or px is None or py is None:
            continue
        routes = find_route_options(model, name, target, max_routes=1)
        if not routes:
            continue
        route = routes[0]
        route_points = [str(value) for value in route.get("points", ())]
        if not route_points or route_points[0] != name or route_points[-1] != target:
            continue
        free_distance = math.hypot(px - float(x), py - float(y))
        route_distance = float(route.get("distance_m", 0.0) or 0.0)
        candidates.append(
            {
                "entry": name,
                "x": px,
                "y": py,
                "theta": _finite(item.get("dir")),
                "free_distance_m": free_distance,
                "route_distance_m": route_distance,
                "total_distance_m": free_distance + route_distance,
                "points": route_points,
                "preferred": name == preferred,
            }
        )
    candidates.sort(
        key=lambda item: (
            0 if item["preferred"] else 1,
            item["total_distance_m"],
            item["free_distance_m"],
            item["entry"],
        )
    )
    return candidates[: max(1, min(5, int(max_entries)))]

def normalize_map(
    raw: Mapping[str, Any], *, max_normal_points: int = DEFAULT_MAX_NORMAL_POINTS
) -> Dict[str, Any]:
    """Return a compact, safe view model from a SEER downloaded map body."""

    if not isinstance(raw, Mapping):
        raise ValueError("SEER map response must be a JSON object")
    header = raw.get("header") if isinstance(raw.get("header"), Mapping) else {}
    normal_raw = raw.get("normalPosList")
    normal_items = normal_raw if isinstance(normal_raw, list) else []
    normal: List[List[float]] = []
    for item in _sample(normal_items, max(1, int(max_normal_points))):
        point = _xy(item)
        if point is not None:
            normal.append([point[0], point[1]])

    advanced_points: List[Dict[str, Any]] = []
    point_items = raw.get("advancedPointList")
    if isinstance(point_items, list):
        for item in point_items:
            if not isinstance(item, Mapping):
                continue
            point = _xy(item.get("pos"))
            if point is None:
                continue
            advanced_points.append(
                {
                    "name": str(item.get("instanceName", "")),
                    "class": str(item.get("className", "")),
                    "x": point[0],
                    "y": point[1],
                    "dir": _finite(item.get("dir")),
                }
            )

    lines: List[Dict[str, Any]] = []
    line_items = raw.get("advancedLineList")
    if isinstance(line_items, list):
        for item in line_items:
            if not isinstance(item, Mapping):
                continue
            line = item.get("line")
            if not isinstance(line, Mapping):
                continue
            start, end = _xy(line.get("startPos")), _xy(line.get("endPos"))
            if start is None or end is None:
                continue
            lines.append(
                {
                    "class": str(item.get("className", "")),
                    "name": str(item.get("instanceName", "")),
                    "start": [start[0], start[1]],
                    "end": [end[0], end[1]],
                }
            )

    curves: List[Dict[str, Any]] = []
    curve_items = raw.get("advancedCurveList")
    if isinstance(curve_items, list):
        for item in curve_items:
            if not isinstance(item, Mapping):
                continue
            start_obj, end_obj = item.get("startPos"), item.get("endPos")
            start = _xy(start_obj.get("pos")) if isinstance(start_obj, Mapping) else None
            end = _xy(end_obj.get("pos")) if isinstance(end_obj, Mapping) else None
            c1, c2 = _xy(item.get("controlPos1")), _xy(item.get("controlPos2"))
            if None in (start, end, c1, c2):
                continue
            curves.append(
                {
                    "class": str(item.get("className", "")),
                    "name": str(item.get("instanceName", "")),
                    "start_name": str(start_obj.get("instanceName", "")).strip(),
                    "end_name": str(end_obj.get("instanceName", "")).strip(),
                    "start": list(start),
                    "end": list(end),
                    "c1": list(c1),
                    "c2": list(c2),
                    "length_m": _curve_length(
                        item.get("className"), start, c1, c2, end
                    ),
                }
            )

    all_points: List[Tuple[float, float]] = [tuple(point) for point in normal]
    all_points.extend((item["x"], item["y"]) for item in advanced_points)
    for item in lines:
        all_points.extend((tuple(item["start"]), tuple(item["end"])))
    for item in curves:
        all_points.extend(
            (tuple(item["start"]), tuple(item["end"]), tuple(item["c1"]), tuple(item["c2"]))
        )

    min_pos = _xy(header.get("minPos"))
    max_pos = _xy(header.get("maxPos"))
    if min_pos is None or max_pos is None or min_pos[0] >= max_pos[0] or min_pos[1] >= max_pos[1]:
        if all_points:
            xs, ys = zip(*all_points)
            min_pos, max_pos = (min(xs), min(ys)), (max(xs), max(ys))
        else:
            min_pos, max_pos = (-1.0, -1.0), (1.0, 1.0)
    if min_pos[0] == max_pos[0]:
        min_pos, max_pos = (min_pos[0] - 1.0, min_pos[1]), (max_pos[0] + 1.0, max_pos[1])
    if min_pos[1] == max_pos[1]:
        min_pos, max_pos = (min_pos[0], min_pos[1] - 1.0), (max_pos[0], max_pos[1] + 1.0)

    return {
        "schema": MAP_CACHE_SCHEMA,
        "cached_at": time.time(),
        "map_name": str(header.get("mapName", raw.get("map_name", ""))),
        "resolution": _finite(header.get("resolution")),
        "bounds": [min_pos[0], min_pos[1], max_pos[0], max_pos[1]],
        "normal_points": normal,
        "advanced_points": advanced_points,
        "lines": lines,
        "curves": curves,
    }


def write_map_cache(path: Path, raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Atomically write a normalized map view shared with the WebUI process."""

    normalized = normalize_map(raw)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(target))
    return normalized


def read_map_cache(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, Mapping) or value.get("schema") != MAP_CACHE_SCHEMA:
        return None
    return value


def recipe_route_picker_payload(
    cache_path: Path,
    snapshot: Any = None,
    *,
    position_unit: str = "mm",
) -> Dict[str, Any]:
    """Return compact projected map data for the Block Builder route picker."""

    model = read_map_cache(cache_path)
    if model is None:
        return {}
    bounds = model.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return {}
    try:
        min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    except (TypeError, ValueError):
        return {}
    if min_x >= max_x or min_y >= max_y:
        return {}

    width, height, margin = 760.0, 420.0, 28.0
    scale = min(
        (width - 2 * margin) / (max_x - min_x),
        (height - 2 * margin) / (max_y - min_y),
    )
    offset_x = (width - (max_x - min_x) * scale) / 2.0
    offset_y = (height - (max_y - min_y) * scale) / 2.0

    def project(point: Sequence[Any]) -> Tuple[float, float]:
        x, y = float(point[0]), float(point[1])
        return (
            offset_x + (x - min_x) * scale,
            height - offset_y - (y - min_y) * scale,
        )

    points = []
    positions: Dict[str, Tuple[float, float]] = {}
    for item in model.get("advanced_points", ()) or ():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "") or "").strip()
        x, y = _finite(item.get("x")), _finite(item.get("y"))
        if not name or x is None or y is None or name in positions:
            continue
        px, py = project((x, y))
        positions[name] = (x, y)
        points.append({"name": name, "x": px, "y": py})

    edges = []
    for item in model.get("curves", ()) or ():
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("start_name", "") or "").strip()
        target = str(item.get("end_name", "") or "").strip()
        if not source or not target or source == target:
            continue
        try:
            start, c1 = project(item["start"]), project(item["c1"])
            c2, end = project(item["c2"]), project(item["end"])
            length = _finite(item.get("length_m"))
            if length is None:
                length = _curve_length(
                    item.get("class"), item["start"], item["c1"], item["c2"], item["end"]
                )
        except (KeyError, TypeError, ValueError):
            continue
        if length <= 0:
            continue
        if str(item.get("class", "")).strip().lower() == "degeneratebezier":
            midpoint = ((c1[0] + c2[0]) / 2.0, (c1[1] + c2[1]) / 2.0)
            path_data = (
                f"M {start[0]:.2f} {start[1]:.2f} "
                f"Q {c1[0]:.2f} {c1[1]:.2f}, {midpoint[0]:.2f} {midpoint[1]:.2f} "
                f"Q {c2[0]:.2f} {c2[1]:.2f}, {end[0]:.2f} {end[1]:.2f}"
            )
        else:
            path_data = (
                f"M {start[0]:.2f} {start[1]:.2f} "
                f"C {c1[0]:.2f} {c1[1]:.2f}, {c2[0]:.2f} {c2[1]:.2f}, "
                f"{end[0]:.2f} {end[1]:.2f}"
            )
        edges.append(
            {
                "source": source,
                "target": target,
                "distance_m": length,
                "path": path_data,
            }
        )

    current_point = ""
    pose = None
    if snapshot is not None:
        candidate = str(
            getattr(snapshot, "last_node_id", None)
            or getattr(snapshot, "station", None)
            or ""
        ).strip()
        position = positions.get(candidate)
        if position is not None:
            sx = _unit_to_native(getattr(snapshot, "x", 0.0), position_unit)
            sy = _unit_to_native(getattr(snapshot, "y", 0.0), position_unit)
            if math.hypot(position[0] - sx, position[1] - sy) <= POINT_ON_TOLERANCE_M:
                current_point = candidate
            rx, ry = project((sx, sy))
            pose = {"x": rx, "y": ry}

    return {
        "map_name": str(model.get("map_name", "") or ""),
        "width": width,
        "height": height,
        "points": points,
        "edges": edges,
        "current_point": current_point,
        "pose": pose,
    }


def write_active_route(
    path: Path,
    route_points: Sequence[str],
    *,
    order_id: Optional[str] = None,
) -> str:
    """Atomically publish the designated route currently executed by the Adapter.

    ``order_id`` is optional for backwards compatibility, but WebUI-created
    VDA5050 orders store it so an intermediate-node idle state cannot be
    mistaken for completion and a later replacement order can retire the old
    yellow route deterministically.
    """

    points = tuple(str(point).strip() for point in route_points)
    if len(points) < 2 or any(not point for point in points):
        raise ValueError("active SEER route requires at least two named points")
    token = f"{os.getpid()}-{time.time_ns()}"
    payload = {
        "schema": ACTIVE_ROUTE_SCHEMA,
        "token": token,
        "started_at": time.time(),
        "route_points": list(points),
    }
    normalized_order_id = str(order_id or "").strip()
    if normalized_order_id:
        payload["order_id"] = normalized_order_id
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{token}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(target))
    return token


def _read_active_route_payload(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, Mapping) or value.get("schema") != ACTIVE_ROUTE_SCHEMA:
        return None
    points = value.get("route_points")
    if not isinstance(points, list) or len(points) < 2:
        return None
    if any(not isinstance(point, str) or not point.strip() for point in points):
        return None
    return value


def read_active_route(path: Path) -> Tuple[str, ...]:
    """Return the active designated route, or an empty tuple when idle."""

    payload = _read_active_route_payload(path)
    if payload is None:
        return ()
    return tuple(str(point).strip() for point in payload["route_points"])


def reconcile_active_route(
    path: Path,
    snapshot: Any,
    *,
    now: Optional[float] = None,
    idle_grace_sec: float = 5.0,
) -> Tuple[str, ...]:
    """Keep a route across telemetry loss and retire it only from known state.

    A missing/offline snapshot cannot prove that navigation stopped.  In that
    case the marker deliberately survives so the yellow route is restored as
    soon as the WebUI can render live state again.  Once an ONLINE snapshot is
    idle, reaching the final point clears immediately; an idle mismatch gets a
    short grace period to avoid a one-poll race at action startup.
    """

    payload = _read_active_route_payload(path)
    if payload is None:
        return ()
    route = tuple(str(point).strip() for point in payload["route_points"])
    connection = str(getattr(snapshot, "connection_state", "") or "").upper()
    if connection and connection != "ONLINE":
        return route

    # ``connection_state`` is the retained VDA/MQTT connection, not the SEER
    # controller TCP link. During a controller outage MQTT remains ONLINE and
    # the Adapter publishes driving=false plus JIBOT_CONNECTION_LOST. That
    # state cannot prove navigation stopped: the controller may still be
    # executing the already accepted route. Preserve the marker until a later
    # connected state confirms motion or the final point.
    errors = getattr(snapshot, "errors", ()) or ()
    for error in errors:
        if not isinstance(error, Mapping):
            continue
        error_type = str(
            error.get("errorType")
            or error.get("error_type")
            or ""
        ).upper()
        if "CONNECTION_LOST" in error_type:
            return route

    working = str(getattr(snapshot, "working_state", "") or "").upper()
    active = bool(
        getattr(snapshot, "driving", False)
        or getattr(snapshot, "paused", False)
        or working
        in {"DRIVING", "MOVING", "WORKING", "RUNNING", "PAUSED", "BLOCKED"}
    )
    if active or not connection:
        return route

    confirmed_point = str(
        getattr(snapshot, "last_node_id", None)
        or getattr(snapshot, "station", None)
        or ""
    ).strip()
    if confirmed_point == route[-1]:
        clear_active_route(path, token=str(payload.get("token", "")))
        return ()

    marker_order_id = str(payload.get("order_id", "") or "").strip()
    snapshot_order_id = str(getattr(snapshot, "order_id", "") or "").strip()

    # A new non-empty order replacing the one that owns this marker is a
    # deterministic reason to retire the old yellow route.
    if marker_order_id and snapshot_order_id and snapshot_order_id != marker_order_id:
        clear_active_route(path, token=str(payload.get("token", "")))
        return ()

    # SEER/Adapter may briefly publish driving=false / IDLE exactly when a
    # released intermediate node is reached before continuing to the next
    # edge. That is progress, not order completion. Preserve the complete
    # designated route instead of erasing the yellow line at LM3/LM4/etc.
    if confirmed_point and confirmed_point in route[:-1]:
        if not marker_order_id or not snapshot_order_id or snapshot_order_id == marker_order_id:
            return route

    # Remaining VDA5050 node/edge states also prove that the same order has
    # unfinished path even if one telemetry sample reports IDLE.
    if (
        marker_order_id
        and snapshot_order_id == marker_order_id
        and (
            bool(getattr(snapshot, "node_states", ()) or ())
            or bool(getattr(snapshot, "edge_states", ()) or ())
        )
    ):
        return route

    try:
        started_at = float(payload.get("started_at", 0.0))
    except (TypeError, ValueError):
        started_at = 0.0
    if (time.time() if now is None else float(now)) - started_at <= idle_grace_sec:
        return route
    clear_active_route(path, token=str(payload.get("token", "")))
    return ()


def clear_active_route(path: Path, *, token: Optional[str] = None) -> bool:
    """Clear an active-route marker, optionally only for its owning action."""

    target = Path(path)
    if token is not None:
        payload = _read_active_route_payload(target)
        if payload is None or str(payload.get("token", "")) != str(token):
            return False
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True


def _unit_to_native(value: Any, unit: str, *, angle: bool = False) -> float:
    numeric = _finite(value) or 0.0
    if angle:
        return math.radians(numeric) if str(unit).lower() == "deg" else numeric
    return numeric * {"m": 1.0, "cm": 0.01, "mm": 0.001}.get(str(unit).lower(), 1.0)


def _fmt(value: Any, digits: int = 3) -> str:
    numeric = _finite(value)
    return "—" if numeric is None else f"{numeric:.{digits}f}"


def render_map_card(
    cache_path: Path,
    snapshot: Any,
    *,
    position_unit: str = "mm",
    orientation_unit: str = "deg",
    adapter_key: str = "",
    csrf_token: str = "",
    return_to: str = "",
    active_route_path: Optional[Path] = None,
    controller_status: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render one cached SEER map and the current AMR pose as inline SVG."""

    model = read_map_cache(cache_path)
    active_route = (
        reconcile_active_route(active_route_path, snapshot)
        if active_route_path is not None
        else ()
    )
    active_edges = set(zip(active_route, active_route[1:]))
    sx = _unit_to_native(getattr(snapshot, "x", 0.0), position_unit)
    sy = _unit_to_native(getattr(snapshot, "y", 0.0), position_unit)
    stheta = _unit_to_native(
        getattr(snapshot, "theta", 0.0), orientation_unit, angle=True
    )
    battery = getattr(snapshot, "battery_soc", None)
    confirmed_point = str(
        getattr(snapshot, "last_node_id", None)
        or getattr(snapshot, "station", None)
        or ""
    ).strip()
    controller_point = ""
    if isinstance(controller_status, Mapping):
        controller_point = str(
            controller_status.get("current_station", "") or ""
        ).strip()
    if model is None:
        point_text = html.escape(confirmed_point or "—")
        return (
            '<div class="map-card seer-map-card">'
            '<div style="min-height:260px;display:grid;place-items:center;padding:24px;'
            'border:1px dashed #557086;border-radius:12px;background:#07131d">'
            '<div><h3>SEER map loading</h3><p class="muted">CONFIG 19207 / API 4011 '
            '지도 응답을 기다리고 있습니다.</p></div></div>'
            '<div class="pose-detail"><h3>Current AMR pose</h3>'
            f'<div class="detail-row"><span>x / y</span><strong>{_fmt(sx)} / {_fmt(sy)} m</strong></div>'
            f'<div class="detail-row"><span>heading</span><strong>{_fmt(math.degrees(stheta), 1)} deg</strong></div>'
            f'<div class="detail-row"><span>current point</span><strong>{point_text}</strong></div>'
            f'<div class="detail-row"><span>battery</span><strong>{html.escape(str(battery if battery is not None else "—"))}%</strong></div>'
            "</div></div>"
        )

    bounds = model.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        bounds = [-1.0, -1.0, 1.0, 1.0]
    min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    width, height, margin = 1000.0, 620.0, 36.0
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))
    offset_x = (width - (max_x - min_x) * scale) / 2.0
    offset_y = (height - (max_y - min_y) * scale) / 2.0

    def project(point: Sequence[Any]) -> Tuple[float, float]:
        x, y = float(point[0]), float(point[1])
        return offset_x + (x - min_x) * scale, height - offset_y - (y - min_y) * scale

    normal_parts = []
    points = model.get("normal_points")
    if isinstance(points, list):
        for point in points:
            if isinstance(point, list) and len(point) >= 2:
                px, py = project(point)
                normal_parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1.25"/>')

    curve_parts = []
    curve_paths_by_edge: Dict[Tuple[str, str], str] = {}
    active_curve_parts = []
    curves = model.get("curves")
    if isinstance(curves, list):
        for item in curves:
            if not isinstance(item, Mapping):
                continue
            try:
                start, c1 = project(item["start"]), project(item["c1"])
                c2, end = project(item["c2"]), project(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            start_name = str(item.get("start_name", "") or "").strip()
            end_name = str(item.get("end_name", "") or "").strip()
            route_attr = ""
            route_class = ""
            if start_name and end_name:
                edge_key = html.escape(f"{start_name}|{end_name}", quote=True)
                route_attr = f' data-seer-route-edge="{edge_key}"'
                route_classes = ["seer-map-route-edge"]
                route_class = ' class="' + " ".join(route_classes) + '"'
            if str(item.get("class", "")).strip().lower() == "degeneratebezier":
                midpoint = ((c1[0] + c2[0]) / 2.0, (c1[1] + c2[1]) / 2.0)
                path_data = (
                    f'M {start[0]:.2f} {start[1]:.2f} '
                    f'Q {c1[0]:.2f} {c1[1]:.2f}, {midpoint[0]:.2f} {midpoint[1]:.2f} '
                    f'Q {c2[0]:.2f} {c2[1]:.2f}, {end[0]:.2f} {end[1]:.2f}'
                )
            else:
                path_data = (
                    f'M {start[0]:.2f} {start[1]:.2f} '
                    f'C {c1[0]:.2f} {c1[1]:.2f}, '
                    f'{c2[0]:.2f} {c2[1]:.2f}, {end[0]:.2f} {end[1]:.2f}'
                )
            curve_svg = f'<path{route_class}{route_attr} d="{path_data}"/>'
            curve_parts.append(curve_svg)
            if start_name and end_name:
                curve_paths_by_edge.setdefault((start_name, end_name), path_data)

    active_path = ""
    for active_start, active_end in zip(active_route, active_route[1:]):
        segment = curve_paths_by_edge.get((active_start, active_end), "")
        if not segment:
            if active_path:
                active_curve_parts.append(
                    f'<path class="seer-map-route-edge seer-route-active seer-route-highlight" '
                    f'd="{active_path}"/>'
                )
                active_path = ""
            continue
        if not active_path:
            active_path = segment
            continue
        command_positions = [
            position
            for token in (" Q ", " C ")
            if (position := segment.find(token)) >= 0
        ]
        tail = segment[min(command_positions) + 1 :] if command_positions else segment
        active_path += " " + tail
    if active_path:
        active_curve_parts.append(
            f'<path class="seer-map-route-edge seer-route-active seer-route-highlight" '
            f'd="{active_path}"/>'
        )

    line_parts = []
    lines = model.get("lines")
    if isinstance(lines, list):
        for item in lines:
            if not isinstance(item, Mapping):
                continue
            try:
                start, end = project(item["start"]), project(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            line_parts.append(
                f'<line x1="{start[0]:.2f}" y1="{start[1]:.2f}" '
                f'x2="{end[0]:.2f}" y2="{end[1]:.2f}"/>'
            )

    map_key = hashlib.sha256(
        f"{Path(cache_path).resolve()}|{model.get('map_name', '')}".encode("utf-8")
    ).hexdigest()[:16]
    escaped_key = html.escape(str(adapter_key), quote=True)
    escaped_csrf = html.escape(str(csrf_token), quote=True)
    escaped_return = html.escape(
        str(return_to or f"/adapter/{adapter_key}"), quote=True
    )
    landmark_parts = []
    navigation_dialogs = []
    nearest_point: Optional[Tuple[str, float]] = None
    landmark_records: List[Tuple[int, Mapping[str, Any], float, float, str]] = []
    landmarks = model.get("advanced_points")
    if isinstance(landmarks, list):
        for point_index, item in enumerate(landmarks):
            if not isinstance(item, Mapping):
                continue
            point_x = _finite(item.get("x"))
            point_y = _finite(item.get("y"))
            point_name = str(item.get("name", "")).strip()
            if point_x is None or point_y is None:
                continue
            landmark_records.append((point_index, item, point_x, point_y, point_name))
            if point_name:
                distance = math.hypot(point_x - sx, point_y - sy)
                if nearest_point is None or distance < nearest_point[1]:
                    nearest_point = (point_name, distance)

    point_positions = {
        record[4]: (record[2], record[3])
        for record in landmark_records
        if record[4]
    }
    logical_point = controller_point if controller_point in point_positions else confirmed_point
    confirmed_position = point_positions.get(logical_point)
    confirmed_distance = (
        math.hypot(confirmed_position[0] - sx, confirmed_position[1] - sy)
        if confirmed_position is not None
        else None
    )
    on_named_point = bool(
        logical_point
        and confirmed_distance is not None
        and confirmed_distance <= POINT_ON_TOLERANCE_M
    )
    route_source = logical_point if on_named_point else ""
    # Target-only VDA5050 orders intentionally leave source_id empty. The WebUI
    # builds a one-node /order for this case, matching an FMS order rather than
    # sending a private Path Nav command envelope.
    escaped_source = ""

    for point_index, item, point_x, point_y, point_name in landmark_records:
        px, py = project((point_x, point_y))
        label = html.escape(point_name)
        point_attr = html.escape(point_name, quote=True)
        can_navigate = bool(point_name and adapter_key and csrf_token)
        dialog_id = (
            "seer-map-nav-"
            + map_key
            + "-"
            + hashlib.sha256(
                f"{point_index}|{point_name}".encode("utf-8")
            ).hexdigest()[:10]
        )
        if can_navigate:
            routes = [
                route
                for route in find_route_options(model, route_source, point_name)
                if len(route.get("points", ())) >= 2
                and float(route.get("distance_m", 0.0)) > 1e-6
            ]
            route_items = []

            for route_index, route in enumerate(routes):
                route_points = [str(value) for value in route["points"]]
                route_json = html.escape(
                    json.dumps(
                        route_points,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    quote=True,
                )
                route_text = html.escape(" → ".join(route_points))
                route_distance = float(route["distance_m"])
                route_items.append(
                    '<label class="seer-map-route-option">'
                    '<input type="radio" name="navigation_mode" value="path" '
                    'data-seer-nav-label="지정 경로" '
                    f'data-seer-route-points="{route_json}" '
                    f'data-seer-route-source="{html.escape(route_points[0], quote=True)}" required'
                    + (' checked' if route_index == 0 else '')
                    + '>'
                    f'<span><strong>지정 경로 {route_index + 1} · '
                    f'{_fmt(route_distance, 2)} m</strong>'
                    f'<small>{route_text} · VDA5050 nodes[] + edges[] order</small></span></label>'
                )

            # Target-only VDA5050 order remains available as the final option.
            # The unchanged Adapter resolves the named node and lets SEER plan the
            # onboard route, while the FMS-facing input is still a standard order.
            auto_source_label = "현재 위치 자동"
            route_items.append(
                '<label class="seer-map-route-option seer-map-auto-path-option">'
                '<input type="radio" name="navigation_mode" value="path" '
                'data-seer-nav-label="VDA5050 Target Order" required'
                + (' checked' if not routes else '')
                + '>'
                f'<span><strong>VDA5050 Target Order · {auto_source_label} → {label}</strong>'
                '<small>one-node VDA5050 order · Adapter가 이름 있는 목표 node를 처리하고 '
                'SEER가 실제 주행 경로를 계획합니다.</small></span></label>'
            )
            if on_named_point and routes:
                route_intro = (
                    '<p><strong>지정 경로 1</strong>이 기본 선택입니다. 다른 지정 경로나 '
                    '맨 아래 <strong>VDA5050 Target Order</strong>를 선택해서 실행할 수도 있습니다.</p>'
                )
            elif not on_named_point:
                route_intro = (
                    '<p>현재 AMR이 PathPoint나 Path 위에 없어도 <strong>VDA5050 Target Order</strong>를 '
                    '사용할 수 있습니다. WebUI는 목표 node 하나의 VDA5050 order를 만들고 Adapter의 '
                    '표준 order queue를 통해 SEER에 전달합니다.</p>'
                )
            else:
                route_intro = (
                    '<p>지정 경로가 없으므로 <strong>VDA5050 Target Order</strong>가 선택됩니다. '
                    '목표 node 하나의 VDA5050 order로 이동합니다.</p>'
                )
            route_fields = (
                route_intro
                + '<div class="seer-map-route-options">'
                + "".join(route_items)
                + '</div><input type="hidden" name="route_points" value="" disabled>'
                + '<input type="hidden" name="reentry_id" value="">'
                + '<input type="hidden" name="reentry_x" value="">'
                + '<input type="hidden" name="reentry_y" value="">'
                + '<input type="hidden" name="reentry_theta" value="">'
            )
            escaped_target_href = f"#{dialog_id}"
            point_open = (
                f'<a class="seer-map-point" href="{escaped_target_href}" '
                f'xlink:href="{escaped_target_href}" '
                f'data-seer-path-target="{point_attr}" '
                f'data-seer-dialog-id="{dialog_id}" '
                f'data-seer-route-count="{len(routes)}" '
                f'aria-label="{point_attr} 포인트 이동 방식 선택">'
            )
            point_close = "</a>"
            navigation_dialogs.append(
                f'<div id="{dialog_id}" class="seer-map-nav-dialog" role="dialog" '
                'aria-modal="true" aria-label="지도 이동 방식 선택">'
                '<div class="seer-map-nav-modal"><h3>이동 방식 선택</h3>'
                f'<p>목적지: <strong>{label}</strong></p>'
                '<div class="seer-map-nav-actions">'
                '<form method="post" data-seer-map-nav-form '
                f'action="/adapter/{escaped_key}/action">'
                f'{route_fields}'
                '<label class="seer-map-waypoint-delay"><span>중간 node 대기 (초)</span>'
                '<input type="number" name="waypoint_delay_sec" value="0" min="0" max="3600" step="0.1">'
                '<small>0보다 크면 중간 node의 actions[]에 blockingType=HARD인 seerWait Action을 넣습니다.</small></label>'
                '<p class="seer-map-nav-status" data-seer-map-nav-status '
                'aria-live="polite"></p>'
                f'<input type="hidden" name="csrf_token" value="{escaped_csrf}">'
                '<input type="hidden" name="action_type" value="vdaOrderRoute">'
                '<input type="hidden" name="confirm" value="on">'
                f'<input type="hidden" name="id" value="{point_attr}">'
                f'<input type="hidden" name="source_id" value="{escaped_source}">'
                '<input type="hidden" name="task_id" value="">'
                f'<input type="hidden" name="return_to" value="{escaped_return}">'
                '<div class="seer-map-nav-buttons"><a class="btn" role="button" href="#content" '
                'data-seer-map-nav-cancel>아니오</a>'
                '<button type="submit" class="btn primary">선택한 방식으로 이동</button>'
                '</div></form></div></div></div>'
            )
        else:
            point_open = '<g class="seer-map-point">'
            point_close = "</g>"
        landmark_parts.append(
            f'{point_open}<g data-seer-map-fixed-point data-seer-map-point-x="{px:.2f}" '
            f'data-seer-map-point-y="{py:.2f}" transform="translate({px:.2f} {py:.2f})">'
            f'<circle cx="0" cy="0" r="16" fill="#ffd166" stroke="#111" stroke-width="1" '
            'data-seer-point-marker data-seer-point-base-radius="16"><title>{label}</title></circle>'
            '<text x="0" y="0" text-anchor="middle" dominant-baseline="central" '
            'stroke="none" fill="#07131d" data-seer-point-label '
            'data-seer-point-label-base="11" '
            f'style="font-weight:800;font-size:11px;font-family:sans-serif;pointer-events:none">{label}</text></g>{point_close}'
        )

    robot_x, robot_y = project((sx, sy))
    heading_deg = -math.degrees(stheta)
    map_name = html.escape(str(model.get("map_name") or getattr(snapshot, "map_id", "") or "—"))
    grid_id = f"seer-grid-{map_key}"
    cached_at = _finite(model.get("cached_at"))
    updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cached_at)) if cached_at else "—"
    counts = f'{len(normal_parts)} pixels · {len(landmark_parts)} points · {len(curve_parts)} paths'
    if controller_point and controller_point in point_positions:
        if on_named_point:
            current_point = f'RoboShop {html.escape(controller_point)}'
        else:
            current_point = (
                f'RoboShop {html.escape(controller_point)} · off {_fmt(confirmed_distance)} m'
            )
    elif logical_point and on_named_point:
        current_point = html.escape(logical_point)
    elif logical_point and confirmed_distance is not None:
        current_point = (
            f'off {html.escape(logical_point)} · {_fmt(confirmed_distance)} m'
        )
    elif nearest_point is not None:
        current_point = (
            f'nearest {html.escape(nearest_point[0])} · {_fmt(nearest_point[1])} m'
        )
    else:
        current_point = "—"
    navigation_help = "마우스 휠로 확대·축소하고, 확대된 지도는 드래그해 이동합니다."
    if navigation_dialogs:
        navigation_help += (
            " 포인트를 누르면 지정 경로가 있으면 첫 번째 VDA5050 route order가 기본 선택되고, 없으면 "
            "목표 node 하나의 VDA5050 Target Order가 선택됩니다. 중간 대기시간을 주면 node actions[]에 seerWait가 포함됩니다."
        )
    if active_route:
        navigation_help = (
            "노란색은 실행 중인 지정 경로입니다: "
            + " → ".join(active_route)
            + ". "
            + navigation_help
        )
    navigation_dialog = "".join(navigation_dialogs)
    svg = (
        f'<svg class="seer-map-svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'data-seer-map-key="{map_key}" '
        f'viewBox="0 0 {int(width)} {int(height)}" role="img" '
        f'aria-label="SEER map {map_name}" style="width:100%;height:auto;display:block;'
        'background:#07131d;border:1px solid #294357;border-radius:12px;'
        'cursor:grab;touch-action:none;user-select:none">'
        f'<defs><pattern id="{grid_id}" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M 40 0 L 0 0 0 40" fill="none" stroke="#173044" stroke-width="1"/>'
        f'</pattern></defs><rect width="100%" height="100%" fill="url(#{grid_id})"/>'
        f'<g fill="#71879a" opacity=".72">{"".join(normal_parts)}</g>'
        f'<g data-seer-route-base-layer fill="none" stroke="#23b8d1" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">{"".join(curve_parts)}</g>'
        f'<g data-seer-route-highlight-layer fill="none" stroke-linecap="round" stroke-linejoin="round">{"".join(active_curve_parts)}</g>'
        f'<g stroke="#ff5c6c" stroke-width="4" stroke-dasharray="9 6">{"".join(line_parts)}</g>'
        f'<g>{"".join(landmark_parts)}</g>'
        f'<g transform="translate({robot_x:.2f} {robot_y:.2f})">'
        f'<g data-seer-robot-marker data-seer-robot-base-transform="rotate({heading_deg:.2f})" '
        f'transform="rotate({heading_deg:.2f}) scale(1)">'
        '<circle r="16" fill="#3ce6a4" opacity=".22"/><path d="M 18 0 L -12 -10 L -6 0 L -12 10 Z" '
        'fill="#3ce6a4" stroke="#eafff6" stroke-width="2"/></g></g>'
        '</svg>'
    )
    return (
        f'<div class="map-card seer-map-card" data-seer-adapter-key="{escaped_key}" '
        f'data-seer-path-source="{escaped_source}" style="display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:16px">'
        f'<div><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px">'
        f'<strong>SEER map · {map_name}</strong><div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">'
        f'<span class="muted">{html.escape(counts)}</span>'
        '<div class="seer-map-zoom-controls" role="group" aria-label="SEER map zoom" '
        'style="display:flex;align-items:center;gap:4px">'
        '<button type="button" class="btn" data-seer-map-zoom="out" title="지도 축소" aria-label="지도 축소">−</button>'
        '<span class="seer-map-zoom-value" aria-live="polite" style="min-width:46px;text-align:center">100%</span>'
        '<button type="button" class="btn" data-seer-map-zoom="in" title="지도 확대" aria-label="지도 확대">＋</button>'
        '<button type="button" class="btn" data-seer-map-zoom="fit" title="전체 지도 맞춤">맞춤</button>'
        '</div><label class="seer-map-robot-size" title="AMR 위치 화살표 크기">화살표 '
        '<input type="range" min="35" max="150" step="5" value="100" '
        'data-seer-robot-size aria-label="AMR 위치 화살표 크기">'
        '<span class="seer-map-robot-size-value" aria-live="polite">100%</span></label>'
        '<label class="seer-map-point-size" title="LM 포인트 원 크기">LM 원 '
        '<input type="range" min="35" max="200" step="5" value="100" '
        'data-seer-point-size aria-label="LM 포인트 원 크기">'
        '<span class="seer-map-point-size-value" aria-live="polite">100%</span></label>'
        '<label class="seer-map-point-label-size" title="LM 포인트 이름 글자 크기">LM 글자 '
        '<input type="range" min="25" max="150" step="5" value="100" '
        'data-seer-point-label-size aria-label="LM 포인트 이름 글자 크기">'
        '<span class="seer-map-point-label-size-value" aria-live="polite">100%</span></label>'
        '<label class="seer-map-route-layer-order" title="파란 기본 경로와 노란 선택/실행 경로의 겹침 순서">경로 겹침 '
        '<select data-seer-route-layer-order aria-label="노란 경로 표시 순서">'
        '<option value="above">노란선 위</option><option value="below">노란선 아래</option>'
        '</select></label>'
        f'</div></div>{svg}'
        f'<p class="muted" style="margin:7px 0 0">{navigation_help}</p>{navigation_dialog}</div>'
        '<div class="pose-detail"><h3>Current AMR pose</h3>'
        f'<div class="detail-row"><span>x / y</span><strong>{_fmt(sx)} / {_fmt(sy)} m</strong></div>'
        f'<div class="detail-row"><span>heading</span><strong>{_fmt(math.degrees(stheta), 1)} deg</strong></div>'
        f'<div class="detail-row"><span>current point</span><strong>{current_point}</strong></div>'
        f'<div class="detail-row"><span>battery</span><strong>{html.escape(str(battery if battery is not None else "—"))}%</strong></div>'
        f'<div class="detail-row"><span>bounds</span><strong>{_fmt(min_x)}..{_fmt(max_x)} m<br>{_fmt(min_y)}..{_fmt(max_y)} m</strong></div>'
        f'<div class="detail-row"><span>map cached</span><strong>{html.escape(updated)}</strong></div>'
        '<p class="muted">지도는 API 4011, 위치는 상태 API의 m/rad 값을 사용합니다.</p>'
        "</div></div>"
    ) + _SEER_MAP_NAV_STYLE


def render_fleet_map_card(
    cache_path: Path,
    selected_snapshot: Any,
    robots: Sequence[Mapping[str, Any]],
    *,
    position_unit: str = "mm",
    orientation_unit: str = "deg",
    adapter_key: str = "",
    csrf_token: str = "",
    active_route_path: Optional[Path] = None,
) -> str:
    """Render one shared map with every same-map SEER AMR overlaid.

    ``adapter_key`` selects the robot that receives point-click navigation.
    Other robots remain visible and can be selected from the toolbar without
    opening a second WebUI.
    """

    card = render_map_card(
        cache_path,
        selected_snapshot,
        position_unit=position_unit,
        orientation_unit=orientation_unit,
        adapter_key=adapter_key,
        csrf_token=csrf_token,
        return_to="/fleet-map",
        active_route_path=active_route_path,
    )
    model = read_map_cache(cache_path)
    if not isinstance(model, Mapping):
        return card
    bounds = model.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return card
    min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    width, height, margin = 1000.0, 620.0, 36.0
    if max_x <= min_x or max_y <= min_y:
        return card
    scale = min(
        (width - 2 * margin) / (max_x - min_x),
        (height - 2 * margin) / (max_y - min_y),
    )
    offset_x = (width - (max_x - min_x) * scale) / 2.0
    offset_y = (height - (max_y - min_y) * scale) / 2.0

    def project(x: float, y: float) -> Tuple[float, float]:
        return (
            offset_x + (x - min_x) * scale,
            height - offset_y - (y - min_y) * scale,
        )

    palette = ("#52d6ff", "#ff6b8a", "#f5cf58", "#b993ff", "#65e197", "#ff9f5c")
    extra_markers = []
    selector_items = []
    map_name = str(model.get("map_name", "") or "").strip()
    hidden_count = 0
    for index, item in enumerate(robots):
        key = str(item.get("key", ""))
        label = str(item.get("label", key) or key)
        snapshot = item.get("snapshot")
        selected = key == adapter_key
        snap_map = str(getattr(snapshot, "map_id", "") or "").strip()
        same_map = not (map_name and snap_map and snap_map != map_name)
        online = bool(getattr(snapshot, "adapter_online", True)) if snapshot is not None else False
        battery = getattr(snapshot, "battery_soc", None) if snapshot is not None else None
        state_class = "good" if online and same_map else "warn"
        href = "/fleet-map?" + urllib.parse.urlencode({"robot": key, "refresh": "1"})
        selector_items.append(
            f'<a class="seer-fleet-robot {"selected" if selected else ""}" '
            f'href="{html.escape(href, quote=True)}" aria-current="{"page" if selected else "false"}">'
            f'<span class="seer-fleet-dot {state_class}" style="--robot-color:{palette[index % len(palette)]}"></span>'
            f'<strong>{html.escape(label)}</strong><small>{"ONLINE" if online else "OFFLINE"}'
            f'{" · 다른 지도" if not same_map else ""}'
            f'{" · " + _fmt(battery, 0) + "%" if battery is not None else ""}</small></a>'
        )
        if snapshot is None or not same_map:
            hidden_count += 1
            continue
        unit = str(item.get("position_unit", position_unit))
        angle_unit = str(item.get("orientation_unit", orientation_unit))
        x = _unit_to_native(getattr(snapshot, "x", 0.0), unit)
        y = _unit_to_native(getattr(snapshot, "y", 0.0), unit)
        theta = _unit_to_native(
            getattr(snapshot, "theta", 0.0), angle_unit, angle=True
        )
        px, py = project(x, y)
        color = palette[index % len(palette)]
        label_markup = html.escape(label)
        # The selected robot already has the green navigation marker created by
        # render_map_card.  Add its name only; add complete colored arrows for
        # every other robot.
        if selected:
            extra_markers.append(
                f'<text x="{px:.2f}" y="{py - 23:.2f}" text-anchor="middle" '
                'paint-order="stroke" stroke="#07131d" stroke-width="4" '
                f'fill="#eafff6" style="font:700 13px sans-serif">{label_markup}</text>'
            )
            continue
        heading_deg = -math.degrees(theta)
        extra_markers.append(
            f'<g transform="translate({px:.2f} {py:.2f})">'
            f'<g data-seer-robot-marker data-seer-robot-base-transform="rotate({heading_deg:.2f})" '
            f'transform="rotate({heading_deg:.2f}) scale(1)">'
            f'<circle r="16" fill="{color}" opacity=".25"/>'
            f'<path d="M 18 0 L -12 -10 L -6 0 L -12 10 Z" fill="{color}" '
            'stroke="#ffffff" stroke-width="2"/></g>'
            f'<text x="0" y="-23" text-anchor="middle" paint-order="stroke" '
            f'stroke="#07131d" stroke-width="4" fill="{color}" '
            f'style="font:700 13px sans-serif">{label_markup}</text></g>'
        )

    svg_end = card.find("</svg>")
    if svg_end >= 0:
        card = card[:svg_end] + "".join(extra_markers) + card[svg_end:]
    card = card.replace("<strong>SEER map ·", "<strong>SEER Fleet map ·", 1)
    warning = (
        f'<p class="muted">{hidden_count}대는 선택 지도와 map_id가 다르거나 상태가 없어 '
        '지도에서 숨겼습니다.</p>'
        if hidden_count
        else '<p class="muted">모든 표시 AMR은 같은 SEER map_id와 좌표계를 사용합니다.</p>'
    )
    toolbar = (
        '<section class="seer-fleet-toolbar"><div><h1>SEER Fleet Map</h1>'
        '<p class="subtitle">한 지도에서 여러 AMR을 확인합니다. 선택한 AMR만 포인트 클릭 '
        'Path Nav 명령을 받습니다.</p></div><div class="seer-fleet-robots">'
        + "".join(selector_items)
        + f'</div>{warning}</section>'
        '<style>.seer-fleet-toolbar{margin-bottom:14px}.seer-fleet-robots{display:grid;'
        'grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-top:10px}'
        '.seer-fleet-robot{display:grid;grid-template-columns:auto 1fr;column-gap:8px;align-items:center;'
        'padding:9px 11px;border:1px solid #294357;border-radius:10px;background:#0d1b26;color:inherit;'
        'text-decoration:none}.seer-fleet-robot.selected{border-color:#52d6ff;box-shadow:0 0 0 2px rgba(82,214,255,.14)}'
        '.seer-fleet-robot small{grid-column:2;color:#9db2c2}.seer-fleet-dot{width:12px;height:12px;border-radius:50%;'
        'background:var(--robot-color);box-shadow:0 0 0 3px rgba(255,255,255,.08)}'
        '.seer-fleet-dot.warn{filter:grayscale(.85);opacity:.55}</style>'
    )
    return toolbar + card
