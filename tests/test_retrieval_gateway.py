"""
测试统一检索出口 - 三段式置信度护栏
"""
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from knowledge.retrieval_gateway import search_with_confidence, get_retrieval_thresholds


def test_retrieval_gateway():
    """测试统一检索出口"""

    print("=" * 80)
    print("统一检索出口测试")
    print("=" * 80)
    print()

    # 读取当前阈值配置
    thresholds = get_retrieval_thresholds()
    print("当前阈值配置:")
    print(f"  高置信度阈值: {thresholds['high_threshold']}")
    print(f"  低置信度阈值: {thresholds['low_threshold']}")
    print(f"  灰区动作: {thresholds['gray_action']}")
    print()

    # 测试用例
    test_cases = [
        ("键盘大概多少钱", "正例 - 预期: high 或 gray"),
        ("能便宜点吗", "正例 - 预期: high"),
        ("什么时候发货", "正例 - 预期: high"),
        ("支持货到付款吗", "负例 - 预期: not_found"),
        ("能开发票吗", "负例 - 预期: not_found"),
        ("保修几年", "负例 - 预期: not_found"),
    ]

    print("=" * 80)
    print("测试案例")
    print("=" * 80)
    print()

    for query, description in test_cases:
        print(f"【测试】 {description}")
        print(f"问题: {query}")

        result = search_with_confidence(query, top_k=3)

        status = result['status']
        score = result['confidence_score']
        action = result['action']

        print(f"结果:")
        print(f"  状态: {status}")
        print(f"  分数: {score:.4f}")
        print(f"  动作: {action}")

        if status == 'high':
            print(f"  ✓ 高置信度 - 可以作答")
            print(f"  检索结果: {len(result['results'])} 个")
            if result['results']:
                top1 = result['results'][0]
                print(f"    Top-1: {top1['id'][:50]}... (分数: {top1['score']:.4f})")
        elif status == 'gray':
            print(f"  ⚠️  灰区 - {result.get('message', '转人工处理')}")
        else:
            print(f"  ✗ 无可靠答案 - {result.get('message', '转人工')}")

        print()

    print("=" * 80)
    print("三段式护栏统计")
    print("=" * 80)
    print()

    results_summary = []
    for query, _ in test_cases:
        result = search_with_confidence(query, top_k=3)
        results_summary.append(result['status'])

    high_count = results_summary.count('high')
    gray_count = results_summary.count('gray')
    not_found_count = results_summary.count('not_found')

    print(f"测试案例总数: {len(test_cases)}")
    print(f"  high (可作答): {high_count} ({high_count/len(test_cases)*100:.1f}%)")
    print(f"  gray (转人工): {gray_count} ({gray_count/len(test_cases)*100:.1f}%)")
    print(f"  not_found (无答案): {not_found_count} ({not_found_count/len(test_cases)*100:.1f}%)")
    print()

    print("=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    test_retrieval_gateway()
