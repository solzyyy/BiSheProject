"""
角色定义生成器 👥✨

职责：
- 从 character_personas.json 生成 characters.rpy
- 可选：把 enhanced_paths 里出现的所有说者都补上 define，避免 Ren'Py 报 Sayer 未定义
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Set, Optional


def collect_speakers_from_path_data(path_data_list: List[Dict[str, Any]]) -> Set[str]:
    """从多条路径的 events[].dialogue[].speaker 收集所有说者（去重）。"""
    seen: Set[str] = set()
    for path_data in path_data_list or []:
        for ev in path_data.get("events") or []:
            for d in ev.get("dialogue") or []:
                if not isinstance(d, dict):
                    continue
                speaker = (d.get("speaker") or "").strip()
                if speaker and speaker != "叙述者（描述）":
                    seen.add(speaker)
    return seen


class CharacterGenerator:
    """角色定义生成器"""
    
    # 角色颜色映射（为不同角色分配不同颜色）
    COLOR_PALETTE = [
        "#DAA520",  # 金色 - 主要角色
        "#8B4513",  # 棕色 - 次要角色
        "#FFD700",  # 金黄色 - 重要角色
        "#8B7355",  # 灰褐色 - 普通角色
        "#696969",  # 灰色 - 背景角色
        "#CD853F",  # 秘鲁色
        "#A0522D",  # 赭色
        "#8B4513",  # 马鞍棕色
        "#654321",  # 深棕色
        "#D2691E",  # 巧克力色
    ]
    
    def __init__(
        self,
        personas_file: str,
        output_file: str,
        extra_speakers: Optional[Set[str]] = None,
        sprite_vars_file: Optional[str] = None,
    ):
        """
        初始化角色生成器

        Args:
            personas_file: character_personas.json 文件路径
            output_file: 输出的 characters.rpy 文件路径
            extra_speakers: 额外说者集合（如从 enhanced_paths 收集），会一并生成 define，避免未定义
            sprite_vars_file: 立绘变量列表文件路径（每行一个变量名，由 import_assets.py 生成 sprite_vars.txt）；
                若提供且存在，则这些变量在 define 时会加上 image="变量名"，说话时显示立绘
        """
        self.personas_file = Path(personas_file)
        self.output_file = Path(output_file)
        self.extra_speakers = extra_speakers or set()
        self.sprite_vars_file = Path(sprite_vars_file) if sprite_vars_file else None
    
    def load_personas(self) -> List[Dict[str, Any]]:
        """加载角色人设数据"""
        with open(self.personas_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("personas", [])

    def get_defined_var_names(self) -> set:
        """返回会在 characters.rpy 中 define 的变量名集合，用于判断某 speaker 是否有定义。"""
        names = set()
        for p in self.load_personas():
            if p.get("name"):
                names.add(self.generate_character_name(p.get("name", "")))
        for s in self.extra_speakers:
            names.add(self._unique_var_name(s, names))
        return names

    def generate_character_name(self, name: str) -> str:
        """
        生成角色变量名（Python 变量名格式）
        
        规则：
        - 中文角色名转换为拼音或使用英文名
        - 特殊字符替换为下划线
        - 转换为小写
        """
        # 简单映射（可根据需要扩展；未在此的 speaker 在脚本中会以字符串形式输出，避免未定义）
        name_mapping = {
            "王佛": "wangfo",
            "林": "lin",
            "皇帝": "emperor",
            "天子": "emperor",  # 同义词
            "僧道": "sengdao",
            "卫兵": "weibing",
            "卫士": "weishi",
            "士兵": "shibing",
            "士兵头目": "shibing_toumu",
            "太监": "taijian",
            "客店老板": "kedian_laoban",
            "庄稼人": "zhuangjiaren",
            "林的妻子": "lin_wife",
            "老百姓": "laobaixing",
            "达官贵人": "daguan_guiren",
            "过路人": "guoluren",
        }
        
        # 如果映射中存在，直接使用
        if name in name_mapping:
            return name_mapping[name]
        
        # 否则，简单处理：替换空格为下划线，转换为小写
        var_name = name.replace(" ", "_").replace("的", "_").lower()
        # 移除特殊字符（保留中文等会变成 _，再合并多余下划线）
        var_name = ''.join(c if c.isalnum() or c == '_' else '_' for c in var_name)
        var_name = var_name.strip("_") or "speaker"
        return var_name

    def _unique_var_name(self, speaker: str, used: Set[str]) -> str:
        """生成唯一变量名，若与 used 冲突则加数字后缀。"""
        base = self.generate_character_name(speaker)
        if base not in used:
            return base
        for i in range(1, 999):
            cand = f"{base}_{i}"
            if cand not in used:
                return cand
        return f"{base}_x"

    def _load_sprite_vars(self) -> Set[str]:
        """读取 sprite_vars.txt（每行一个变量名；以 # 开头的行忽略）。文件不存在或为空则返回空集合。"""
        if not self.sprite_vars_file or not self.sprite_vars_file.is_file():
            return set()
        try:
            with open(self.sprite_vars_file, "r", encoding="utf-8") as f:
                return {line.strip() for line in f if line.strip() and not line.strip().startswith("#")}
        except Exception:
            return set()

    def generate_characters_rpy(self) -> str:
        """生成 characters.rpy 文件内容（personas + enhanced_paths 中出现的所有说者）。"""
        personas = self.load_personas()
        sprite_vars = self._load_sprite_vars()
        used_var_names: Set[str] = set()
        lines = []
        lines.append("# 角色定义")
        lines.append("# 此文件由脚本自动生成，请勿手动修改")
        lines.append("")

        def _char_line(var_name: str, name: str, color: str) -> str:
            if var_name in sprite_vars:
                return f'define {var_name} = Character("{name}", image="{var_name}", color="{color}")'
            return f'define {var_name} = Character("{name}", color="{color}")'

        # 1) personas 中的角色
        for i, persona in enumerate(personas):
            name = persona.get("name", "")
            if not name:
                continue
            var_name = self.generate_character_name(name)
            if var_name in used_var_names:
                var_name = self._unique_var_name(name, used_var_names)
            used_var_names.add(var_name)
            color = self.COLOR_PALETTE[i % len(self.COLOR_PALETTE)]
            lines.append(_char_line(var_name, name, color))

        # 2) extra_speakers（enhanced_paths 里出现但不在 personas 的说者）全部补上
        persona_names = {p.get("name", "").strip() for p in personas if p.get("name")}
        extra_sorted = sorted(self.extra_speakers)
        for j, speaker in enumerate(extra_sorted):
            if not speaker or speaker in persona_names:
                continue
            var_name = self.generate_character_name(speaker)
            if var_name in used_var_names:
                var_name = self._unique_var_name(speaker, used_var_names)
            used_var_names.add(var_name)
            color = self.COLOR_PALETTE[(len(personas) + j) % len(self.COLOR_PALETTE)]
            lines.append(_char_line(var_name, speaker, color))
        
        lines.append("")
        return "\n".join(lines)
    
    def save(self):
        """保存生成的文件"""
        content = self.generate_characters_rpy()
        
        # 确保输出目录存在
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.output_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"已生成角色定义文件：{self.output_file}")

