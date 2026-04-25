from __future__ import annotations # 未来类型注解模式 在类型标注里可以提前引用还没定义的类

import json
import os
from typing import Any, Dict, List

from neo4j import GraphDatabase, Driver
from dotenv import load_dotenv


load_dotenv() # 把 .env 里的东西读进来


class Neo4jClient:
    """
    Neo4j 数据库客户端封装类 🗄️
    
    这个类的作用是：
    1. 封装 Neo4j 数据库连接和操作
    2. 提供简洁的 API 来操作知识图谱（事件、人物、状态变化等）
    3. 隐藏底层 Cypher 查询的复杂性
    4. 管理数据库索引和约束
    
    使用方式：
        client = Neo4jClient()
        client.upsert_event(event_data)
        client.upsert_character(character_data)
        client.upsert_state_change(state_change_data)
        client.close()
    """

    def __init__(self, *, quiet: bool = False) -> None:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        if uri.startswith("neo4j://"):
            uri = uri.replace("neo4j://", "bolt://", 1)
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            self._driver.verify_connectivity()
        except Exception as e:
            if not quiet:
                print(f"[red]Neo4j connection failed: {e}[/red]")
                print("[yellow]Please check:[/yellow]")
                print(f"[yellow]  1. Neo4j is running[/yellow]")
                print(f"[yellow]  2. NEO4J_URI is correct (current: {uri})[/yellow]")
                print(f"[yellow]  3. NEO4J_USER and NEO4J_PASSWORD are correct[/yellow]")
            raise

    def close(self) -> None:
        self._driver.close()

    def init_constraints(self) -> None:
        """
        初始化数据库约束，确保事件节点唯一性 🔒
        
        同时创建 Event 节点的索引以优化查询性能：
        - id 唯一约束（自动创建索引）
        - is_start 索引（用于快速查找起始事件）
        """
        queries = [
            # 🆔 Event ID 唯一约束（自动创建索引，用于 ORDER BY e.id）
            """
            CREATE CONSTRAINT event_id IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE;
            """,
            
            # 🎯 is_start 索引（用于快速查找起始事件）
            # 用于 canonical_branch.py 的 get_first_event_id() 查询
            """
            CREATE INDEX event_is_start IF NOT EXISTS
            FOR (e:Event)
            ON (e.is_start);
            """,
        ]
        
        names = ["event_id 唯一约束", "event_is_start 索引"]
        with self._driver.session() as session:
            for i, query in enumerate(queries, 1):
                try:
                    session.run(query)
                    if i <= len(names):
                        print(f"  ✅ {names[i-1]} 创建成功")
                except Exception as e:
                    # 如果约束或索引已存在，忽略错误
                    error_msg = str(e)
                    if "already exists" in error_msg.lower() or "equivalent" in error_msg.lower():
                        print(f"  ℹ️  {names[i-1] if i <= len(names) else '约束/索引'} 已存在，跳过")
                    else:
                        print(f"  ⚠️  创建 {names[i-1] if i <= len(names) else '约束/索引'} 时出现警告: {error_msg[:100]}")
    
    def init_state_change_indexes(self) -> None:
        """
        初始化 StateChange 节点的索引 🎯✨
        
        创建以下索引以优化查询性能：
        1. 定位索引：通过 target_type 和 target_id 快速定位状态变化（支持 character, relationship, world）
        2. 事件索引：通过 source_event 查询状态变化（用于 get_event_state_changes()）
        3. target_type 单独索引：用于按 target_type 查询（用于 _get_world_state_changes()）
        4. 状态维度索引：通过 target_id 和 dimension 查询特定维度的变化
        5. 复合索引：用于排序和组合查询（source_event + target_type + dimension）
        6. 剧情敏感度索引：用于 RAG 检索（如果字段存在）
        
        这些索引对所有类型的 StateChange 都有效：
        - character: 人物内在状态
        - relationship: 人物关系状态
        - world: 世界/物品状态
        """
        queries = [
            # 🎯 定位索引（必须）- 通过 target_type 和 target_id 快速定位
            """
            CREATE INDEX state_target IF NOT EXISTS
            FOR (s:StateChange)
            ON (s.target_type, s.target_id);
            """,
            
            # ⏳ 事件索引（必须）- 通过 source_event 查询状态变化
            # 用于 get_event_state_changes() 查询
            """
            CREATE INDEX state_source_event IF NOT EXISTS
            FOR (s:StateChange)
            ON (s.source_event);
            """,
            
            # 🌍 target_type 单独索引（优化）- 用于按 target_type 查询
            # 用于 _get_world_state_changes() 查询 WHERE s.target_type = 'world'
            # 虽然 state_target 索引包含 target_type，但单独索引在只按 target_type 查询时更高效
            """
            CREATE INDEX state_target_type IF NOT EXISTS
            FOR (s:StateChange)
            ON (s.target_type);
            """,
            
            # 🧠 状态维度索引（强烈推荐）- 查询特定维度的变化
            """
            CREATE INDEX state_target_dimension IF NOT EXISTS
            FOR (s:StateChange)
            ON (s.target_id, s.dimension);
            """,
            
            # 📊 复合索引（优化）- 用于排序和组合查询
            # 用于 get_event_state_changes() 的 ORDER BY s.target_type, s.dimension
            # 以及可能的组合查询 WHERE source_event = ? AND target_type = ?
            """
            CREATE INDEX state_source_target_dimension IF NOT EXISTS
            FOR (s:StateChange)
            ON (s.source_event, s.target_type, s.dimension);
            """,
            
            # 🎭 剧情敏感度索引（可选）- 用于 RAG 检索
            # 注意：如果 StateChange 节点还没有 plot_sensitive 字段，这个索引会创建但暂时不会使用
            # 等后续添加该字段后，索引会自动生效
            """
            CREATE INDEX state_plot_sensitive IF NOT EXISTS
            FOR (s:StateChange)
            ON (s.plot_sensitive);
            """,
        ]
        
        with self._driver.session() as session:
            for i, query in enumerate(queries, 1):
                try:
                    session.run(query)
                    index_names = [
                        "state_target", 
                        "state_source_event", 
                        "state_target_type",
                        "state_target_dimension",
                        "state_source_target_dimension",
                        "state_plot_sensitive"
                    ]
                    if i <= len(index_names):
                        print(f"  [green]索引创建成功: {index_names[i-1]}[/green]")
                except Exception as e:
                    # 如果索引已存在或其他错误，记录但不中断
                    # 对于 plot_sensitive 索引，如果字段不存在，Neo4j 5.x+ 会创建但暂时不使用
                    error_msg = str(e)
                    # 忽略路由信息相关的警告（通常是 Neo4j 集群配置问题，不影响单机使用）
                    if "routing information" in error_msg.lower():
                        print(f"  [cyan]索引创建成功（路由信息警告可忽略）[/cyan]")
                    elif "already exists" in error_msg.lower() or "equivalent" in error_msg.lower():
                        print(f"  [cyan]索引已存在，跳过[/cyan]")
                    else:
                        print(f"  [yellow]创建索引时出现警告: {error_msg[:100]}[/yellow]")
    
    def init_character_constraints_and_indexes(self) -> None:
        """
        初始化 Character 节点的约束和索引 🆔✨
        
        创建以下约束和索引：
        1. ID 唯一约束：确保角色 ID 唯一且极速检索
        2. 别名索引：用于角色名称消歧义和检索
        """
        queries = [
            # 🆔 确保 ID 唯一且极速检索
            """
            CREATE CONSTRAINT char_id_unique IF NOT EXISTS 
            FOR (c:Character) 
            REQUIRE c.id IS UNIQUE;
            """,
            
            # 🔍 别名检索（为了消歧义）
            """
            CREATE INDEX char_aliases IF NOT EXISTS 
            FOR (c:Character) 
            ON (c.aliases);
            """,
        ]
        
        with self._driver.session() as session:
            for i, query in enumerate(queries, 1):
                try:
                    session.run(query)
                    names = ["char_id_unique 约束", "char_aliases 索引"]
                    if i <= len(names):
                        print(f"  ✅ {names[i-1]} 创建成功")
                except Exception as e:
                    # 如果约束或索引已存在，忽略错误
                    error_msg = str(e)
                    # 忽略路由信息相关的警告（通常是 Neo4j 集群配置问题，不影响单机使用）
                    if "routing information" in error_msg.lower():
                        print(f"  ℹ️  约束/索引创建成功（路由信息警告可忽略）")
                    elif "already exists" in error_msg.lower() or "equivalent" in error_msg.lower():
                        print(f"  ℹ️  约束/索引已存在，跳过")
                    else:
                        print(f"  ⚠️  创建约束/索引时出现警告: {error_msg[:100]}")
    
    def drop_all_indexes_and_constraints(self) -> None:
        """
        删除所有索引和约束 🗑️
        
        警告：这会删除所有索引和约束！请谨慎使用。
        通常在重建索引前，如果想要一个干净的状态，可以调用此方法。
        """
        print("[yellow]🗑️  开始删除所有索引和约束...[/yellow]")
        
        # 需要删除的索引和约束列表
        items_to_drop = [
            # Event 约束和索引
            ("CONSTRAINT", "event_id"),
            ("INDEX", "event_is_start"),
            # StateChange 索引
            ("INDEX", "state_target"),
            ("INDEX", "state_source_event"),
            ("INDEX", "state_target_type"),
            ("INDEX", "state_target_dimension"),
            ("INDEX", "state_source_target_dimension"),
            ("INDEX", "state_plot_sensitive"),
            # Character 约束和索引
            ("CONSTRAINT", "char_id_unique"),
            ("INDEX", "char_aliases"),
        ]
        
        with self._driver.session() as session:
            for item_type, item_name in items_to_drop:
                try:
                    if item_type == "CONSTRAINT":
                        query = f"DROP CONSTRAINT {item_name} IF EXISTS"
                    else:  # INDEX
                        query = f"DROP INDEX {item_name} IF EXISTS"
                    
                    session.run(query)
                    print(f"  ✅ 已删除 {item_type}: {item_name}")
                except Exception as e:
                    error_msg = str(e)
                    if "does not exist" in error_msg.lower() or "not found" in error_msg.lower():
                        print(f"  ℹ️  {item_type} {item_name} 不存在，跳过")
                    else:
                        print(f"  ⚠️  删除 {item_type} {item_name} 时出现警告: {error_msg[:100]}")
        
        print("[green]✅ 所有索引和约束删除完成！[/green]")
    
    def init_all_indexes(self) -> None:
        """
        初始化所有索引和约束 🚀
        
        一次性创建所有必要的索引和约束，包括：
        - Event 节点的约束
        - StateChange 节点的索引
        - Character 节点的约束和索引
        """
        print("[cyan]🔧 开始初始化数据库索引和约束...[/cyan]")
        
        # 初始化 Event 约束
        print("📌 创建 Event 节点约束...")
        try:
            self.init_constraints()
            print("  ✅ event_id 约束创建成功")
        except Exception as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower() or "equivalent" in error_msg.lower():
                print("  ℹ️  约束已存在，跳过")
            else:
                print(f"  ⚠️  创建约束时出现警告: {error_msg[:100]}")
        
        # 初始化 StateChange 索引
        print("[cyan]🎯 创建 StateChange 节点索引...[/cyan]")
        self.init_state_change_indexes()
        
        # 初始化 Character 约束和索引
        print("[cyan]🆔 创建 Character 节点约束和索引...[/cyan]")
        self.init_character_constraints_and_indexes()
        
        print("[green]✅ 所有索引和约束初始化完成！[/green]")

    def upsert_event(self, event: Dict[str, Any]) -> None:
        """
        创建或更新事件节点 📅
        
        将事件数据扁平化存储为节点属性，方便查询和分析。
        这样在 Neo4j Browser 中可以直接查看，也方便写 Cypher 查询做分析。
        """
        core = event.get("core", {})
        metrics = event.get("metrics", {})
        flags = event.get("flags", {})
        
        # 提取对话信息（从 core 中，如果存在）
        dialogue_info = core.get("对话信息")
        has_dialogue = bool(dialogue_info)
        dialogue_participants = []
        dialogue_type = ""
        dialogue_purpose = ""
        dialogue_content_json = None
        
        if dialogue_info:
            dialogue_participants = dialogue_info.get("participants", [])
            dialogue_type = dialogue_info.get("dialogue_type", "")
            dialogue_purpose = dialogue_info.get("dialogue_purpose", "")
            # dialogue_content 是数组，结构复杂，保留为 JSON
            dialogue_content = dialogue_info.get("dialogue_content", [])
            if dialogue_content:
                dialogue_content_json = json.dumps(dialogue_content, ensure_ascii=False)
        
        
        query = """
        MERGE (e:Event {id: $id}) // 查找标签为 Event 且 id 等于参数 $id 的节点
        SET e.type = $type,
            // 核心属性（扁平化）
            e.人物 = $人物,
            e.行动 = $行动,
            e.目标 = $目标,
            e.结果 = $结果,
            e.情绪 = $情绪,
            e.时间 = $时间,
            e.场景 = $场景,
            e.前提条件 = $前提条件,
            e.结果影响 = $结果影响,
            e.source_text = $source_text,
            e.风格 = $风格,
            e.文化元素 = $文化元素,
            // 对话信息（扁平化）
            e.has_dialogue = $has_dialogue,
            e.dialogue_participants = $dialogue_participants,
            e.dialogue_type = $dialogue_type,
            e.dialogue_purpose = $dialogue_purpose,
            // 指标属性
            e.emotion_value = $emotion_value,
            e.conflict_value = $conflict_value,
            e.impact_value = $impact_value,
            e.metrics_说明 = $metrics_说明,
            // 标志属性
            e.is_start = $is_start,
            e.is_ending = $is_ending,
            e.ending_id = $ending_id
        """
        
        params = {
            "id": event["id"],
            "type": event.get("type", "Event"),
            # 核心属性
            "人物": core.get("人物", []),
            "行动": core.get("行动", ""),
            "目标": core.get("目标", ""),
            "结果": core.get("结果", ""),
            "情绪": core.get("情绪", ""),
            "时间": core.get("时间", ""),
            "场景": core.get("场景", ""),
            "前提条件": core.get("前提条件", ""),
            "结果影响": core.get("结果影响", ""),
            "source_text": core.get("source_text", ""),
            "风格": core.get("风格", []),
            "文化元素": core.get("文化元素", []),
            # 对话信息（扁平化）
            "has_dialogue": has_dialogue,
            "dialogue_participants": dialogue_participants,
            "dialogue_type": dialogue_type,
            "dialogue_purpose": dialogue_purpose,
            # 指标属性
            "emotion_value": metrics.get("emotion_value", 0),
            "conflict_value": metrics.get("conflict_value", 0),
            "impact_value": metrics.get("impact_value", 0),
            "metrics_说明": metrics.get("说明", ""),
            # 标志属性
            "is_start": flags.get("is_start", False),
            "is_ending": flags.get("is_ending", False),
            "ending_id": flags.get("ending_id"),
        }
        
        # dialogue_content 结构复杂（数组中的对象），保留为 JSON
        if dialogue_content_json:
            params["dialogue_content_json"] = dialogue_content_json
            query += ",\n            e.dialogue_content_json = $dialogue_content_json"
        
        with self._driver.session() as session:
            session.run(query, **params) # 执行查询  **params 是参数解包

    def link_relation(self, relation: Dict[str, Any]) -> None:
        """
        建立事件之间的关系 🔗
        
        在 Neo4j 中创建或更新两个事件之间的关系。
        关系类型统一为 REL，具体的关系类型（因果、时间顺序等）存储在 r.type 属性中。
        """
        query = """
        MATCH (a:Event {id: $from}), (b:Event {id: $to})
        MERGE (a)-[r:REL]->(b)
        SET r.type = $type,
            r.描述 = $desc
        """
        params = {
            "from": relation["from"],
            "to": relation["to"],
            "type": relation.get("type", "时间顺序"),
            "desc": relation.get("描述", ""),
        }
        
        # 注意：constraints 字段在 schema 中定义但实际数据中未使用，所以这里不处理
        # 如果未来需要使用，可以在这里添加处理逻辑
        
        with self._driver.session() as session:
            session.run(query, **params)

    def clear_all_data(self) -> None:
        """
        清空数据库中的所有数据 🗑️
        
        警告：这会删除所有节点和关系！请谨慎使用。
        通常在导入新数据前，如果想要一个干净的知识图谱，可以调用此方法。
        """
        query = """
        MATCH (n)
        DETACH DELETE n
        """
        with self._driver.session() as session:
            session.run(query)
        print("[yellow]⚠️  已清空数据库中的所有数据[/yellow]")
    
    def get_all_events(self) -> List[Dict[str, Any]]:
        """
        从 Neo4j 中获取所有事件 📅
        
        Returns:
            事件列表，格式与 JSON 中的事件格式兼容
        """
        query = """
        MATCH (e:Event)
        RETURN e.id as event_id,
               e.人物 as 人物,
               e.行动 as 行动,
               e.目标 as 目标,
               e.结果 as 结果,
               e.情绪 as 情绪,

               e.时间 as 时间,
               e.场景 as 场景,
               e.前提条件 as 前提条件,
               e.结果影响 as 结果影响,
               e.source_text as source_text,
               e.风格 as 风格,
               e.文化元素 as 文化元素,
               e.emotion_value as emotion_value,
               e.conflict_value as conflict_value,
               e.impact_value as impact_value,
               e.metrics_说明 as metrics_说明,
               e.has_dialogue as has_dialogue,
               e.dialogue_type as dialogue_type,
               e.dialogue_participants as dialogue_participants,
               e.dialogue_purpose as dialogue_purpose,
               e.dialogue_content_json as dialogue_content_json
        ORDER BY e.id
        """
        with self._driver.session() as session:
            result = session.run(query)
            events = []
            for record in result:
                event_data = dict(record)

                
                event = {
                    "event_id": event_data.get("event_id", ""),
                    "事件": {
                        "人物": event_data.get("人物", []),
                        "行动": event_data.get("行动", ""),
                        "目标": event_data.get("目标", ""),
                        "结果": event_data.get("结果", ""),
                        "情绪": event_data.get("情绪", ""),
                        "时间": event_data.get("时间", ""),
                        "场景": event_data.get("场景", ""),
                        "前提条件": event_data.get("前提条件", ""),
                        "结果影响": event_data.get("结果影响", ""),
                        "source_text": event_data.get("source_text", ""),
                        "风格": event_data.get("风格", []),
                        "文化元素": event_data.get("文化元素", []),
                    },
                    "数值属性": {
                        "emotion_value": event_data.get("emotion_value", 0),
                        "conflict_value": event_data.get("conflict_value", 0),
                        "impact_value": event_data.get("impact_value", 0),
                        "说明": event_data.get("metrics_说明", ""),
                    },
                }
                
                # 添加对话信息（如果有）
                if event_data.get("has_dialogue"):
                    dialogue_content = None
                    if event_data.get("dialogue_content_json"):
                        try:
                            dialogue_content = json.loads(event_data["dialogue_content_json"])
                        except Exception:
                            pass
                    
                    event["对话信息"] = {
                        "has_dialogue": True,
                        "dialogue_info": {
                            "participants": event_data.get("dialogue_participants", []),
                            "dialogue_type": event_data.get("dialogue_type", ""),
                            "dialogue_purpose": event_data.get("dialogue_purpose", ""),
                            "dialogue_content": dialogue_content or [],
                        }
                    }
                else:
                    event["对话信息"] = {
                        "has_dialogue": False,
                        "dialogue_info": None
                    }
                
                events.append(event)
            return events
    
    def get_all_relations(self) -> List[Dict[str, Any]]:
        """
        从 Neo4j 中获取所有事件关系 🔗
        
        Returns:
            关系列表，格式与 JSON 中的关系格式兼容
        """
        query = """
        MATCH (a:Event)-[r:REL]->(b:Event)
        RETURN a.id as from,
               b.id as to,
               r.type as 类型,
               r.描述 as 描述
        ORDER BY a.id, b.id
        """
        with self._driver.session() as session:
            result = session.run(query)
            relations = []
            for record in result:
                rel_data = dict(record)
                relation = {
                    "from": rel_data.get("from", ""),
                    "to": rel_data.get("to", ""),
                    "类型": rel_data.get("类型", "时间顺序"),
                    "描述": rel_data.get("描述", ""),
                }
                relations.append(relation)
            return relations
    
    def upsert_character(self, entity: Dict[str, Any]) -> None:
        """
        创建或更新 Character 节点 👤
        
        Args:
            entity: 实体数据，包含 id, canonical_name, aliases, entity_type 等
        """
        query = """
        MERGE (c:Character {id: $id})
        SET c.canonical_name = $canonical_name,
            c.aliases = $aliases,
            c.mention_count = $mention_count,
            c.entity_type = $entity_type
        """
        params = {
            "id": entity.get("id"),
            "canonical_name": entity.get("canonical_name", ""),
            "aliases": entity.get("aliases", []),
            "mention_count": entity.get("mention_count", 0),
            "entity_type": entity.get("entity_type", ""),
        }
        
        with self._driver.session() as session:
            session.run(query, **params)
    
    def upsert_state_change(self, state_change: Dict[str, Any]) -> None:
        """
        创建或更新 StateChange 节点 🎯
        
        Args:
            state_change: 状态变化数据，符合 StateChange 模型
        """
        # 处理 target_id：如果是列表，转换为字符串（用于存储）
        target_id = state_change.get("target_id")
        if isinstance(target_id, list):
            target_id_str = json.dumps(target_id, ensure_ascii=False)
        else:
            target_id_str = str(target_id)
        
        # 处理 condition：如果有，转换为 JSON 字符串
        condition = state_change.get("condition")
        condition_json = None
        if condition:
            condition_json = json.dumps(condition, ensure_ascii=False)
        
        query = """
        MERGE (s:StateChange {
            source_event: $source_event,
            target_type: $target_type,
            target_id: $target_id,
            dimension: $dimension
        })
        SET s.change_type = $change_type,
            s.value = $value,
            s.condition = $condition,
            s.is_obstacle = $is_obstacle,
            s.logic_impact = $logic_impact,
            s.plot_sensitive = $plot_sensitive
        WITH s
        MATCH (e:Event {id: $source_event})
        MERGE (e)-[:TRIGGERS]->(s)
        """
        
        params = {
            "source_event": state_change.get("source_event"),
            "target_type": state_change.get("target_type"),
            "target_id": target_id_str,
            "dimension": state_change.get("dimension"),
            "change_type": state_change.get("change_type"),
            "value": state_change.get("value"),
            "condition": condition_json,
            "is_obstacle": state_change.get("is_obstacle", False),
            "logic_impact": state_change.get("logic_impact", ""),
            "plot_sensitive": state_change.get("plot_sensitive"),
        }
        
        with self._driver.session() as session:
            session.run(query, **params)
        
        # 根据 target_type 创建不同的关系
        target_type = state_change.get("target_type")
        if target_type == "character":
            # 创建 Character 节点与 StateChange 的关系
            char_id = str(target_id) if not isinstance(target_id, list) else target_id[0]
            query = """
            MATCH (s:StateChange {
                source_event: $source_event,
                target_type: $target_type,
                target_id: $target_id,
                dimension: $dimension
            })
            MATCH (c:Character {id: $char_id})
            MERGE (c)-[:HAS_STATE]->(s)
            """
            with self._driver.session() as session:
                session.run(query, {
                    "source_event": state_change.get("source_event"),
                    "target_type": target_type,
                    "target_id": target_id_str,
                    "dimension": state_change.get("dimension"),
                    "char_id": char_id,
                })
        elif target_type == "relationship":
            # 创建两个 Character 节点与 StateChange 的关系
            if isinstance(target_id, list) and len(target_id) == 2:
                char_a_id, char_b_id = target_id[0], target_id[1]
                query = """
                MATCH (s:StateChange {
                    source_event: $source_event,
                    target_type: $target_type,
                    target_id: $target_id,
                    dimension: $dimension
                })
                MATCH (a:Character {id: $char_a_id})
                MATCH (b:Character {id: $char_b_id})
                MERGE (a)-[:HAS_RELATIONSHIP_STATE]->(s)
                MERGE (b)-[:HAS_RELATIONSHIP_STATE]->(s)
                """
                with self._driver.session() as session:
                    session.run(query, {
                        "source_event": state_change.get("source_event"),
                        "target_type": target_type,
                        "target_id": target_id_str,
                        "dimension": state_change.get("dimension"),
                        "char_a_id": char_a_id,
                        "char_b_id": char_b_id,
                    })
        elif target_type == "world":
            # World 类型的 StateChange 不需要与 Character 节点关联
            # 它们已经通过 TRIGGERS 关系与 Event 关联（在上面的查询中已创建）
            # World 状态是全局的，不依赖于特定角色
            pass





