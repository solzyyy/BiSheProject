"""
智能别名映射器 🌸✨

使用规则筛选 + LLM 确认的方式识别别名关系。
策略：先用规则筛选出少数可疑候选，再用LLM逐个确认。
"""

import json
import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

from character.models.mention import Mention
from core.llm_client import AsyncLLMClient


class AliasMapper:
    """智能别名映射器 🎯✨"""
    
    def __init__(
        self, 
        llm_client: AsyncLLMClient,
        mentions: List[Mention],
        threshold: float = 0.7
    ):
        """
        初始化别名映射器
        
        Args:
            llm_client: LLM 客户端实例
            mentions: Mention 列表
            threshold: LLM 确认阈值（置信度）
        """
        self.llm_client = llm_client
        self.mentions = mentions
        self.threshold = threshold
        
        # 按名字分组
        self.name_groups: Dict[str, List[Mention]] = defaultdict(list)
        for mention in mentions:
            self.name_groups[mention.name].append(mention)
    
    def smart_filter_candidates(self) -> List[Tuple[str, str, str]]:
        """
        智能筛选候选别名对
        
        核心原则：
        1. 如果两个名字在同一事件共现过 → 绝对不是别名
        2. 只基于通用特征，不依赖特定词汇
        
        通用特征：
        - 名字包含关系
        - 字符重叠度
        - role_hint 重叠度
        - 从未共现
        
        Returns:
            候选对列表：[(name1, name2, reason), ...]
        """
        candidates = []
        names = list(self.name_groups.keys())
        
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                name1, name2 = names[i], names[j]
                
                # 提取特征
                events1 = set(m.event_id for m in self.name_groups[name1])
                events2 = set(m.event_id for m in self.name_groups[name2])
                
                roles1 = set(m.role_hint for m in self.name_groups[name1] if m.role_hint)
                roles2 = set(m.role_hint for m in self.name_groups[name2] if m.role_hint)
                
                # 🚫 核心过滤：如果共现过，绝对不是别名
                cooccur = len(events1 & events2) > 0
                if cooccur:
                    continue
                
                # 从未共现，开始判断是否可疑
                
                # 规则 1: 名字包含关系（最可靠）
                if name1 in name2 or name2 in name1:
                    # 计算 role 重叠度
                    if roles1 and roles2:
                        role_overlap = len(roles1 & roles2) / min(len(roles1), len(roles2))
                        if role_overlap > 0:  # 有任何重叠
                            candidates.append((name1, name2, "名字包含+角色重叠"))
                            continue
                    # 即使没有role重叠，如果一个名字完全包含另一个，也值得确认
                    elif len(name1) >= 2 and len(name2) >= 2:
                        candidates.append((name1, name2, "名字包含关系"))
                        continue
                
                # 规则 2: 名字字符重叠度高 + role 重叠度高
                chars1, chars2 = set(name1), set(name2)
                common_chars = chars1 & chars2
                
                if common_chars and roles1 and roles2:
                    # 字符重叠度：共同字符数 / 较短名字的字符数
                    char_overlap = len(common_chars) / min(len(chars1), len(chars2))
                    
                    # role 重叠度
                    role_overlap = len(roles1 & roles2) / min(len(roles1), len(roles2))
                    
                    # 如果两者都有一定重叠，可能是别名
                    if char_overlap >= 0.3 and role_overlap >= 0.3:
                        candidates.append((name1, name2, f"相似度高(字:{char_overlap:.1%},角色:{role_overlap:.1%})"))
                        continue
                
                # 规则 3:role 高度重叠
                if roles1 and roles2:
                    role_overlap = len(roles1 & roles2) / min(len(roles1), len(roles2))
                    # 如果 role 高度重叠（≥80%），即使没共同字也可能是别名
                    if role_overlap >= 0.8:
                        candidates.append((name1, name2, f"角色高度重叠({role_overlap:.0%})"))
                        continue
                
                # 规则 4: 名字高度相似（即使role不重叠）
                if common_chars and len(common_chars) >= 2:  # 至少2个共同字
                    char_overlap = len(common_chars) / min(len(chars1), len(chars2))
                    if char_overlap >= 0.5:  # 字符重叠≥50%
                        candidates.append((name1, name2, f"名字高度相似(共{len(common_chars)}字)"))
                        continue
        
        return candidates
    
    def build_llm_prompt(self, name1: str, name2: str) -> str:
        """构建 LLM 判断提示词"""
        mentions1 = self.name_groups[name1]
        mentions2 = self.name_groups[name2]
        
        # 收集信息
        role_hints1 = list(set(m.role_hint for m in mentions1 if m.role_hint))
        role_hints2 = list(set(m.role_hint for m in mentions2 if m.role_hint))
        
        contexts1 = [m.context_snippet[:120] for m in mentions1[:2] if m.context_snippet]
        contexts2 = [m.context_snippet[:120] for m in mentions2[:2] if m.context_snippet]
        
        prompt = f"""判断以下两个名字是否指代同一个角色个体（别名关系）。

**名字1**: {name1}
- 出现次数: {len(mentions1)}
- 角色定位: {', '.join(role_hints1) if role_hints1 else '无'}
- 上下文示例:
{chr(10).join(f"  • {ctx}" for ctx in contexts1) if contexts1 else '  无'}

**名字2**: {name2}
- 出现次数: {len(mentions2)}
- 角色定位: {', '.join(role_hints2) if role_hints2 else '无'}
- 上下文示例:
{chr(10).join(f"  • {ctx}" for ctx in contexts2) if contexts2 else '  无'}

**判断标准**:
1. 同一个体的不同称呼 → 是别名
2. 同类角色的集合 → 可能是别名（如果指同一群体）
3. 完全不同的角色 → 不是别名

请以 JSON 格式返回（仅返回JSON，无其他内容）：
{{
  "is_alias": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "简短理由（中文，30字内）"
}}"""
        
        return prompt
    
    async def ask_llm(self, name1: str, name2: str) -> Dict[str, Any]:
        """调用 LLM 判断"""
        if self.llm_client.use_stub:
            return {"is_alias": False, "confidence": 0.0, "reasoning": "API未配置"}
        
        prompt = self.build_llm_prompt(name1, name2)
        
        try:
            result = await self.llm_client.invoke(prompt=prompt, return_json=True)
            return result
        except Exception as e:
            return {"is_alias": False, "confidence": 0.0, "reasoning": f"错误: {str(e)}"}
    
    async def confirm_with_llm(
        self, 
        candidates: List[Tuple[str, str, str]]
    ) -> List[Dict[str, Any]]:
        """用 LLM 逐个确认候选对"""
        confirmed_pairs = []
        total = len(candidates)
        plain = os.environ.get("PIPELINE_UI_PROGRESS", "").lower() == "plain" or (
            not sys.stdout.isatty()
        )

        for idx, (name1, name2, reason) in enumerate(candidates, 1):
            result = await self.ask_llm(name1, name2)
            if plain and total > 0:
                print(f"[entities] {idx}/{total}", flush=True)

            if result.get('is_alias') and result.get('confidence', 0) >= self.threshold:
                confirmed_pairs.append({
                    'name1': name1,
                    'name2': name2,
                    'confidence': result.get('confidence'),
                    'reasoning': result.get('reasoning'),
                    'filter_reason': reason
                })
        
        return confirmed_pairs
    
    def merge_aliases(
        self, 
        confirmed_pairs: List[Dict[str, Any]]
    ) -> Dict[str, List[str]]:
        """合并别名组（使用并查集）"""
        parent = {}
        
        def find(x: str) -> str:
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]
        
        def union(x: str, y: str):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py
        
        for pair in confirmed_pairs:
            union(pair['name1'], pair['name2'])
        
        # 分组
        groups = defaultdict(list)
        for name in self.name_groups.keys():
            root = find(name)
            groups[root].append(name)
        
        # 选择规范名（出现次数最多的）
        canonical_groups = {}
        for root, aliases in groups.items():
            sorted_aliases = sorted(
                aliases,
                key=lambda n: len(self.name_groups[n]),
                reverse=True
            )
            canonical = sorted_aliases[0]
            canonical_groups[canonical] = sorted_aliases
        
        return canonical_groups
    
    def generate_entities(
        self, 
        canonical_groups: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        生成实体表 🌸✨
        
        优化后的字段：
        - aliases: 仅存真实别名（不包含 canonical_name）
        - entity_type: 新增角色分类（主角/配角/路人）
        """
        entities = []
        entity_id = 1
        
        # 第一步：收集所有实体的 mention_count，计算平均值
        mention_counts = []
        for canonical, aliases in canonical_groups.items():
            all_mentions = []
            for alias in aliases:
                all_mentions.extend(self.name_groups[alias])
            mention_counts.append(len(all_mentions))
        
        # 计算平均 mention_count（用于判断标准）
        total_avg = sum(mention_counts) / len(mention_counts) if mention_counts else 0
        
        # 第二步：生成实体，使用平均值判断
        for canonical, aliases in sorted(canonical_groups.items()):
            # 收集该实体的所有 mentions
            all_mentions = []
            for alias in aliases:
                all_mentions.extend(self.name_groups[alias])
            
            mention_count = len(all_mentions)
            
            # 1. 优化 aliases：移除 canonical_name，只保留真实别名
            real_aliases = [alias for alias in aliases if alias != canonical]
            
            # 2. 判断 entity_type（角色分类）- 使用平均值倍数判断
            entity_type = self._determine_entity_type(
                mention_count=mention_count,
                total_avg=total_avg
            )
            
            entity = {
                "id": f"C{entity_id:03d}",
                "canonical_name": canonical,
                "aliases": real_aliases,  # 仅存真实别名或为空 []
                "mention_count": mention_count,
                "entity_type": entity_type  # 新增：角色分类
            }
            
            entities.append(entity)
            entity_id += 1
        
        return entities
    
    def _determine_entity_type(
        self,
        mention_count: int,
        total_avg: float
    ) -> str:
        """
        判断实体类型（角色分类）🎭
        
        使用平均值倍数判断，更公平准确！💖
        
        分类标准：
        - 主角：count >= total_avg * 2.5（闪闪发光的主角！💖）
        - 配角：count >= total_avg（重要的支撑！✨）
        - 路人：其他情况（乖巧的背景板喵~🐾）
        """
        # 判断逻辑：使用平均值倍数
        if mention_count >= total_avg * 2.5:
            return "主角"  # 闪闪发光的主角！💖
        elif mention_count >= total_avg:
            return "配角"  # 重要的支撑！✨
        else:
            return "路人"  # 乖巧的背景板喵~🐾
    
    async def run(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        运行完整流程
        
        Returns:
            (entities, confirmed_pairs) 元组
        """
        # 智能筛选候选
        candidates = self.smart_filter_candidates()
        
        if not candidates:
            # 没有候选，所有名字都是独立实体
            canonical_groups = {name: [name] for name in self.name_groups.keys()}
            confirmed_pairs = []
        else:
            # LLM 确认
            confirmed_pairs = await self.confirm_with_llm(candidates)
            # 合并别名
            canonical_groups = self.merge_aliases(confirmed_pairs)
        
        # 生成实体
        entities = self.generate_entities(canonical_groups)
        
        return entities, confirmed_pairs


__all__ = ["AliasMapper"]

