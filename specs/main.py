import sys
import platform


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
    demo()

    print("\n" + "=" * 50)
    if v_ok and m_ok:
        print("🎉 环境检查全部通过！Python 运行正常")
    else:
        print("⚠️  部分检查未通过，请检查环境配置")
    print("=" * 50)