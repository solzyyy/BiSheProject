"""
结局脚本生成器 🎬✨

职责：
- 从路径 JSON 的 ending 字段生成结局脚本（endings/*.rpy）
"""

from pathlib import Path
from typing import Dict, Any, List


class EndingScriptGenerator:
    """结局脚本生成器"""
    
    def __init__(self, character_generator: Any):
        """
        初始化结局脚本生成器
        
        Args:
            character_generator: CharacterGenerator 实例，用于角色名映射
        """
        self.character_generator = character_generator
    
    def escape_renpy_text(self, text: str) -> str:
        """转义 Ren'Py 文本中的特殊字符"""
        if not text:
            return ""
        
        text = text.replace('"', '\\"')
        text = text.replace('{', '{{')
        text = text.replace('[', '[[')
        
        return text
    
    def get_character_var_name(self, speaker: str) -> str:
        """获取角色变量名"""
        return self.character_generator.generate_character_name(speaker)
    
    def generate_ending_script(self, ending: Dict[str, Any]) -> str:
        """
        生成结局脚本内容
        
        Args:
            ending: 结局数据
            
        Returns:
            Ren'Py 脚本内容
        """
        lines = []
        ending_id = ending.get("id", "")
        # 优先使用详细场景描述（LLM 生成），如果没有则使用原始描述
        scene_desc = ending.get("detailed_scene_description") or ending.get("scene_description", "")
        dialogue = ending.get("dialogue", [])
        
        # 生成 label
        label_name = f"ending_{ending_id}"
        lines.append(f"label {label_name}:")
        lines.append("")
        
        # 生成场景描述
        if scene_desc:
            escaped_text = self.escape_renpy_text(scene_desc)
            lines.append(f'    "{escaped_text}"')
            lines.append("")
        
        # 生成对话
        for d in dialogue:
            speaker = d.get("speaker", "")
            text = d.get("text", "")
            
            if not text:
                continue
            
            escaped_text = self.escape_renpy_text(text)
            
            if speaker and speaker != "叙述者（描述）":
                var_name = self.get_character_var_name(speaker)
                lines.append(f'    {var_name} "{escaped_text}"')
            else:
                lines.append(f'    "{escaped_text}"')
        
        if dialogue:
            lines.append("")
        
        # 标记结局已解锁
        lines.append(f"    # 标记结局已解锁")
        lines.append(f"    $ persistent.{ending_id} = True")
        lines.append("")
        
        # 返回主菜单
        lines.append("    return")
        lines.append("")
        
        return "\n".join(lines)
    
    def save_ending_script(self, ending: Dict[str, Any], output_file: Path):
        """
        保存结局脚本
        
        Args:
            ending: 结局数据
            output_file: 输出文件路径
        """
        content = self.generate_ending_script(ending)
        
        # 确保输出目录存在
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        ending_id = ending.get("id", "")
        print(f"已生成结局脚本：{output_file} (label: ending_{ending_id})")

