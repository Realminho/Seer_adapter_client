# json 핸들링을 위한 json 라이브러리 import
import json
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 최단 경로 탐색을 위한 heapq 라이브러리 import
import heapq

# 회피 주행용 AvoidGraphNavigator 클래스 선언
class AvoidGraphNavigator:
    # 클래스 초기화 함수 선언
    def __init__(self,seer,graph_map_path):
        # Seer commu 객체 저장
        self.seer = seer
        # Graph map 경로 저장
        self.graph_map_path = graph_map_path
        # 노드 정보 저장 변수 선언
        self.nodes = {}
        # 엣지 정보 저장 변수 선언
        self.edges = []
        # 차단 엣지 정보 저장 변수 선언
        self.blocked_edges = set()
        # graph map 로드
        self.load_graph_map()

    # graph map 로드 함수 선언
    def load_graph_map(self):
        # JSON으로 정의된 graph_map 파일 열기
        with open(self.graph_map_path, "r", encoding="utf-8") as file:
            graph_map_data = json.load(file)
        # 불러온 json에서 nodes 정보 저장
        self.nodes = graph_map_data.get("nodes",{})
        # 불러온 json에서 edge 정보 저장
        self.edges = graph_map_data.get("edges",[])
        # 디버그 문구 print
        print("--------------------------------------------------")
        print("[GRAPH] graph map load 완료")
        print(f"[GRAPH] node 개수 : {len(self.nodes)}")
        print(f"[GRAPH] edge 개수 : {len(self.edges)}")
        print("--------------------------------------------------")

    # 특정 노드의 좌표 정보 return 함수 선언
    def get_node_cord(self,node_name):
        # node가 없으면 None return
        if node_name not in self.nodes:
            print(f"[GRAPH] Graph map에 정의되지 않은 node : {node_name}")
            return None
        # node 정보 저장
        node_info = self.nodes[node_name]
        # 특정 노드의 x,y,theta return
        return (float(node_info["x"]),float(node_info["y"]),float(node_info["theta"]))

    # 인자로 받은 node의 edge 정보를 찾는 함수 선언
    def get_edge_info(self,from_node,to_node):
        # graph map edges에서 각 edge_info를 저장
        for edge_info in self.edges:
            # enable 값이 False면
            if edge_info.get("enable", True) is not True:
                # 다음 edge로 path
                continue
            # graph map에 정의된 edge면
            if edge_info["from"] == from_node and edge_info["to"] == to_node:
                # edge 정보 return
                return edge_info
            # bidirection이 True이고 
            if edge_info.get("bidirection", False) is True:
                # 인자가 역방향일 경우 
                if edge_info["from"] == to_node and edge_info["to"] == from_node:
                    # 같은 edge로 허용하여 edge 정보 return
                    return edge_info
        # 위 조건에 만족하지 않으면 None return
        return None

    # 특정 edge 차단 함수 선언
    def block_edge(self,from_node,to_node):
        # 차단할 edge 저장 변수에 해당 노드 추가
        self.blocked_edges.add((from_node,to_node))
        # 디버그 문구 print
        print(f"[GRAPH] 차단 edge 등록 완료 : {from_node} -> {to_node}")

    # 차단 edge 초기화 함수 선언
    def clear_blocked_edges(self):
        # 차단 edge 전체 초기화
        self.blocked_edges.clear()
        # 디버그 문구 print
        print("[GRAPH] 차단 edge 초기화 완료")

    # Dijkstra 기반 최단 경로 탐색 노드 함수 선언
    def find_shortest_path(self,start_node,goal_node):
        # 시작 노드 또는 목표 노드가 graph map에 없으면
        if start_node not in self.nodes or goal_node not in self.nodes:
            # 디버그 문구 print
            print(f"[GRAPH] start 또는 goal node 오류 : {start_node} -> {goal_node}")
            # 빈 리스트 return
            return []
        # 우선순위 큐 선언
        priority_queue = []
        # 시작 노드 push (누적 cost, 현재 node)
        heapq.heappush(priority_queue,(0.0,start_node))
        # 각 node까지 최소 cost 저장 dict 선언
        min_cost = {node_name: float("inf") for node_name in self.nodes.keys()}
        # 시작 node cost는 0으로 설정
        min_cost[start_node] = 0.0
        # 경로 복원용 이전 node 저장 dict 선언
        prev_node = {}
        # queue가 빌때까지 반복
        while priority_queue:
            # 현재 cost, node pop
            current_cost,current_node = heapq.heappop(priority_queue)
            # 현재 cost가 이미 저장된 최소 cost보다 크면
            if current_cost > min_cost[current_node]:
                # skip
                continue
            # 목표 노드에 도착하면 종료
            if current_node == goal_node:
                break
            # graph map edges에서 각 edge_info를 저장
            for edge_info in self.edges:
                # enable 값이 False면 해당 edge는 skip
                if edge_info.get("enable", True) is not True:
                    continue
                # 정방향 edge인 경우
                if edge_info["from"] == current_node:
                    # 다음 node 저장
                    next_node = edge_info["to"]
                    # 현재 edge가 차단된 경우 skip
                    if (current_node,next_node) in self.blocked_edges:
                        continue
                    # 다음 node까지 cost 계산
                    next_cost = current_cost + float(edge_info.get("cost",1.0))
                    # 다음 노드까지 cost가 현재 cost보더 더 짧은 경로면
                    if next_cost < min_cost[next_node]:
                        # 최소 cost 갱신
                        min_cost[next_node] = next_cost
                        # 현재 노드 추가
                        prev_node[next_node] = current_node
                        # que에 추가
                        heapq.heappush(priority_queue,(next_cost,next_node))                    
                # bidirection이 True이고 역방향으로도 갈 수 있는 경우
                if edge_info.get("bidirection", False) is True and edge_info["to"] == current_node:
                    # 다음 node 저장
                    next_node = edge_info["from"]
                    # 현재 edge가 차단된 경우 skip
                    if (current_node,next_node) in self.blocked_edges:
                        continue
                    # 다음 node까지 cost 계산
                    next_cost = current_cost + float(edge_info.get("cost",1.0))
                    # 더 짧은 경로면 갱신
                    if next_cost < min_cost[next_node]:
                        min_cost[next_node] = next_cost
                        prev_node[next_node] = current_node
                        heapq.heappush(priority_queue,(next_cost,next_node))                    
        # 목표 node까지 갈 수 없으면 빈 리스트 return
        if min_cost[goal_node] == float("inf"):
            print(f"[GRAPH] {start_node} -> {goal_node} 경로 탐색 실패")
            return []
        # 경로 복원 리스트 선언
        path_node_list = []
        # 현재 node를 goal로 설정
        current_node = goal_node
        # prev_node를 따라 역추적
        while current_node in prev_node:
            path_node_list.append(current_node)
            current_node = prev_node[current_node]
        # 시작 node 추가
        path_node_list.append(start_node)
        # 경로 뒤집기
        path_node_list.reverse()
        # 디버그 문구 print
        print(f"[GRAPH] 최단 경로 탐색 완료 : {path_node_list}")
        # 최단 경로 list return
        return path_node_list

    # 경로 list를 순차적으로 주행하는 함수 선언
    def drive_path(self, path_node_list, obstacle_check_func=None, drive_interval=0.2):
        # 작업 완료 상태 상수 선언
        COMPLETED = 4
        # 노드 리스트 길이가 2보다 작으면
        if len(path_node_list) < 2:
            # 디버그 문구 print
            print("[GRAPH] 주행할 경로가 없습니다.")
            # False, None return
            return False, None
        # 경로 list에서 순차적으로 반복
        for node_index in range(len(path_node_list) - 1):
            # 현재 노드 저장
            from_node = path_node_list[node_index]
            # 다음 노드 저장
            to_node = path_node_list[node_index + 1]
            # 현재 노드와 연관된 edge 정보 추출
            edge_info = self.get_edge_info(from_node, to_node)
            # edge 정보가 없으면
            if edge_info is None:
                # 디버그 문구 print
                print(f"[GRAPH] edge 정보 없음 : {from_node} -> {to_node}")
                # 실패 return
                return False, (from_node, to_node)
            # 다음으로 주행할 node 좌표 추출
            target_pose = self.get_node_cord(to_node)
            # 좌표 정보가 없으면 실패 처리
            if target_pose is None:
                # 디버그 문구 print
                print(f"[GRAPH] node 좌표 정보 없음 : {to_node}")
                # 실패 return
                return False, (from_node, to_node)
            # 목표 노드 정보 x, y, theta로 좌표 분리
            target_x, target_y, target_theta = target_pose
            # edge 정보에서 goal_theta 추출
            goal_theta = float(edge_info.get("goal_theta", target_theta))
            # edge 정보에서 back_mode 추출
            back_mode = int(edge_info.get("back_mode", 0))
            # edge 정보에서 drive_mode 추출
            drive_mode = edge_info.get("drive_mode", "forward")
            # 디버그 문구 print
            print("==================================================")
            print(f"[GRAPH] {from_node} -> {to_node} 주행 시작")
            print(f"[GRAPH] target_x : {target_x}")
            print(f"[GRAPH] target_y : {target_y}")
            print(f"[GRAPH] goal_theta : {goal_theta}")
            print(f"[GRAPH] drive_mode : {drive_mode}")
            print(f"[GRAPH] back_mode : {back_mode}")
            print("==================================================")
            # AMR 주행 요청 송신
            self.seer.gopath(x=target_x, y=target_y, theta=goal_theta, back_mode=back_mode)
            # 새 task가 실제로 시작됐는지 확인하는 변수
            task_started_flag = False
            # 명령 직후 상태 반영 시간을 조금 기다림
            time.sleep(0.3)
            # 도착 또는 장애물 감지까지 반복
            while True:
                # 장애물 감지 함수가 있으면
                if obstacle_check_func is not None:
                    # 장애물 감지 함수가 True이면
                    if obstacle_check_func() is True:
                        # 디버그 문구 print
                        print(f"[GRAPH] 주행 중 장애물 감지 : {from_node} -> {to_node}")
                        # 막힌 edge 정보 return
                        return False, (from_node, to_node)
                # 현재 task status 확인
                current_task_status = self.seer.get_task_status()
                # 디버그 출력
                print(f"[GRAPH] current_task_status : {current_task_status}")
                # 응답이 아직 없으면
                if current_task_status is None:
                    time.sleep(drive_interval)
                    continue
                # 한 번이라도 COMPLETED가 아닌 값이 나오면
                # 새 task가 시작된 것으로 판단
                if current_task_status != COMPLETED:
                    task_started_flag = True
                # 새 task가 시작된 이후에 완료가 나오면
                # 그때 진짜 도착 완료 처리
                if task_started_flag is True and self.seer.task_end() is True:
                    # 디버그 문구 print
                    print(f"[GRAPH] {to_node} 도착 완료")
                    # 현재 구간 주행 종료
                    break
                # 주행 주기만큼 대기
                time.sleep(drive_interval)
        # 전체 경로 완료
        return True, None

    # 현재 위치에서 특정 Node로 복귀하는 함수 선언
    def drive_to_node(self,target_node):
        # 목표 노드 좌표 추출
        target_pose = self.get_node_cord(target_node)
        # 좌표 정보가 없으면 False return
        if target_pose is None:
            return False
        # 좌표 분리
        target_x,target_y,target_theta = target_pose
        # 디버그 문구 print
        print("==================================================")
        print(f"[GRAPH] 현재 위치에서 {target_node} 복귀 주행 시작")
        print(f"[GRAPH] target_x : {target_x}")
        print(f"[GRAPH] target_y : {target_y}")
        print(f"[GRAPH] target_theta : {target_theta}")
        print("==================================================")        
        # 현재 위치에서 목표 node로 주행
        self.seer.gopathblock(x=target_x,y=target_y,theta=target_theta,back_mode=1)
        # 디버그 문구 print
        print(f"[GRAPH] {target_node} 복귀 완료")
        # True return
        return True

    # 그래프 기반 회피 주행 함수 선언
    def navigate_with_avoid(self,start_node,goal_node,obstacle_check_func=None):
        # 차단 edge 초기화
        self.clear_blocked_edges()
        # 인자로 받은 노드 기반 최단 경로 탐색
        first_path = self.find_shortest_path(start_node,goal_node)
        # 최초 경로가 없으면 실패
        if not first_path:
            print("[GRAPH] 최초 경로 탐색 실패")
            return False
        # 디버그 문구 print
        print("##################################################")
        print(f"[GRAPH] 최초 계산 경로 : {first_path}")
        print("##################################################")        
        # 최초 경로 주행
        success,blocked_edge = self.drive_path(path_node_list=first_path,obstacle_check_func=obstacle_check_func)
        # 최초 경로 성공 시 종료
        if success is True:
            print("[GRAPH] 최초 경로로 목적지 도착 완료")
            return True
        # edge가 없으면 실패
        if blocked_edge is None:
            print("[GRAPH] edge 확인 실패")
            # False return
            return False
        # 막힌 edge 정보 분리
        blocked_from_node,blocked_to_node = blocked_edge
        # 막힌 edge 차단
        self.block_edge(blocked_from_node,blocked_to_node)
        # 시작 노드로 주행
        return_start = self.drive_to_node(start_node)
        # 시작 노드로 주행이 실패하였으면
        if return_start is not True:
            # 디버그 문구 print
            print(f"[GRAPH] {start_node} 복귀 실패")
            # False return
            return False  
        # 막힌 edge 반영하여 다시 경로 검색
        replanned_path = self.find_shortest_path(start_node,goal_node)
        # 재탐색 경로가 없으면
        if not replanned_path:
            # 디버그 문구 print
            print("[GRAPH] 재탐색 경로 없음")
            # False return
            return False
        # 디버그 문구 print
        print("##################################################")
        print(f"[GRAPH] 재탐색 경로 : {replanned_path}")
        print("##################################################")
        # 재탐색 경로 주행
        success,blocked_edge = self.drive_path(path_node_list=replanned_path,obstacle_check_func=obstacle_check_func)
        # 재탐색 경로 주행이 성공하였으면
        if success is True:
            # 디버그 문구 print
            print("[GRAPH] 재탐색 경로로 목적지 도착 완료")
            # Ture return
            return True
        # 실패하였다면
        else:
            # 디버그 문구 print
            print(f"[GRAPH] 재탐색 경로 주행 실패 : {blocked_edge}")
            # False return
            return False        

                

                