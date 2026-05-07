"""
主脚本更新器 🎯✨

职责：
- 更新 script.rpy，添加路径跳转逻辑
"""

from pathlib import Path
from typing import List, Dict, Any


class MainScriptUpdater:
    """主脚本更新器"""
    
    def __init__(self, script_file: Path):
        """
        初始化主脚本更新器
        
        Args:
            script_file: script.rpy 文件路径
        """
        self.script_file = script_file
    
    def load_existing_script(self) -> str:
        """加载现有的 script.rpy 内容"""
        if not self.script_file.exists():
            return ""
        
        with open(self.script_file, 'r', encoding='utf-8') as f:
            return f.read()
    
    def extract_config_section(self, content: str) -> str:
        """
        提取配置部分（保留原有配置）
        
        保留文件开头的注释和配置，直到 label start: 之前（不包含该行，避免重复定义）
        """
        lines = content.split('\n')
        config_lines = []
        for line in lines:
            if line.strip().startswith('label start:'):
                break
            config_lines.append(line)
        return '\n'.join(config_lines)
    
    def generate_start_label(self, paths: List[Dict[str, Any]]) -> str:
        """
        生成 label start: 部分
        
        Args:
            paths: 所有路径数据列表
            
        Returns:
            label start: 的脚本内容
        """
        lines = []
        lines.append("")
        lines.append("# 游戏在此开始。")
        lines.append("label start:")
        lines.append("")
        
        # 方案1：直接跳转到第一个路径（canonical_path）
        # 查找 canonical_path
        canonical_path = None
        for path in paths:
            if path.get("renpy_label") == "canonical_path":
                canonical_path = path
                break
        
        # 背景音乐
        lines.append('    play music "audio/voice_of_evening.mp3" fadein 1.0')
        lines.append("")

        if canonical_path:
            lines.append("    # 跳转到主线路径")
            lines.append("    jump canonical_path")
        else:
            # 如果没有 canonical_path，跳转到第一个路径
            if paths:
                first_path = paths[0]
                renpy_label = first_path.get("renpy_label", "")
                if renpy_label:
                    lines.append(f"    # 跳转到路径：{renpy_label}")
                    lines.append(f"    jump {renpy_label}")
                else:
                    lines.append("    # 暂无可用路径")
            else:
                lines.append("    # 暂无可用路径")
        
        lines.append("")
        lines.append("    return")
        lines.append("")
        
        return "\n".join(lines)
    
    def update_script(self, paths: List[Dict[str, Any]]):
        """
        更新主脚本
        
        Args:
            paths: 所有路径数据列表
        """
        existing_content = self.load_existing_script()
        
        # 提取配置部分
        config_section = self.extract_config_section(existing_content)
        
        # 生成新的 start label
        start_label = self.generate_start_label(paths)
        
        # 组合内容
        new_content = config_section.rstrip() + "\n" + start_label
        
        # 保存
        with open(self.script_file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"已更新主脚本：{self.script_file}")

