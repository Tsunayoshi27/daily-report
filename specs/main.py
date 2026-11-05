import os
import sys
import platform

_PLACEHOLDER_MARKERS = [
    "在这里填", "找测试同学要", "发件邮箱地址", "邮箱SMTP授权码",
    "your_", "YOUR_", "TODO", "todo",
]


def _is_placeholder(value):
    if not value or not value.strip():
        return True
    for marker in _PLACEHOLDER_MARKERS:
        if marker in value:
            return True
    return False


def check_python_version():
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    print(f"Python 版本: {version_str}")
    print(f"完整信息: {sys.version}")
    print(f"可执行路径: {sys.executable}")
    print(f"操作系统: {platform.system()} {platform.release()} {platform.machine()}")
    print(f"处理器: {platform.processor() or 'N/A'}")
    print()

    if version.major == 3 and version.minor >= 12:
        print(f"✅ Python {version_str} 满足要求 (>= 3.12)")
        return True
    else:
        print(f"❌ Python {version_str} 不满足要求 (需要 >= 3.12)")
        return False


def check_common_modules():
    modules = ["pip", "os", "sys", "json", "datetime", "pathlib"]
    print("\n--- 常用模块检查 ---")
    ok = 0
    for mod in modules:
        try:
            __import__(mod)
            print(f"  ✅ {mod}")
            ok += 1
        except ImportError:
            print(f"  ❌ {mod} 未安装")
    print(f"\n模块加载: {ok}/{len(modules)} 成功")
    return ok == len(modules)


def check_credentials():
    print("\n--- 数据凭证检查（仅验证是否配置，不显示值）---")
    credentials = {
        "GITHUB_PAT":       "GitHub 个人访问令牌",
        "FEISHU_APP_ID":    "飞书应用 ID",
        "FEISHU_APP_SECRET":"飞书应用密钥",
        "SMTP_HOST":        "SMTP 服务器地址",
        "SMTP_PORT":        "SMTP 端口",
        "SMTP_USER":        "发件邮箱",
        "SMTP_AUTH_CODE":   "邮箱 SMTP 授权码",
    }
    ok = 0
    for key, label in credentials.items():
        value = os.environ.get(key, "")
        if value and not _is_placeholder(value):
            print(f"  ✅ {label:20s}  已配置")
            ok += 1
        else:
            print(f"  ⚠️  {label:20s}  未配置或为占位符")
    print(f"\n凭证配置: {ok}/{len(credentials)} 项已就绪")
    return ok


def demo():
    print("\n--- 功能演示 ---")
    from datetime import datetime
    now = datetime.now()
    print(f"  当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    data = {"msg": "Hello, Gitee!", "items": [1, 2, 3]}
    import json
    print(f"  JSON 序列化: {json.dumps(data, ensure_ascii=False)}")

    from pathlib import Path
    p = Path(__file__).resolve().parent
    print(f"  当前目录: {p}")
    print(f"  目录存在: {p.exists()}")


if __name__ == "__main__":
    print("=" * 50)
    print("     Python 环境验证脚本")
    print("=" * 50)

    v_ok = check_python_version()
    m_ok = check_common_modules()
    c_ok = check_credentials()
    demo()

    print("\n" + "=" * 50)
    all_ok = v_ok and m_ok and c_ok >= 2
    if v_ok and m_ok:
        print("🎉 基础环境检查全部通过！")
    else:
        print("⚠️  基础环境检查未通过")
    if c_ok >= 2:
        print("✅ 凭证至少已配置 2 项")
    else:
        print("⚠️  凭证配置不足（至少需要 GitHub PAT）")
    print("=" * 50)