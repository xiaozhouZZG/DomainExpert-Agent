"""
检索评估脚本

评估当前检索系统的 Recall@k 和 MRR
"""
import json
import sys
import io
from pathlib import Path

# 设置 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge.hybrid_rag_engine import get_hybrid_engine


def load_eval_set(path):
    """加载评估集"""
    eval_cases = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                eval_cases.append(json.loads(line))
    return eval_cases


def evaluate_retrieval(eval_cases, top_k_values=[5, 10]):
    """评估检索性能"""
    engine = get_hybrid_engine()

    # 确保索引已构建
    if engine.mode == "vector":
        engine.build_index()

    results = []

    for case in eval_cases:
        question = case['question']
        expected_ids = set(case['expected_chunk_ids'])
        category = case.get('category', 'unknown')
        is_negative = (category == 'negative')

        # 执行检索
        retrieved = engine.search(question, top_k=max(top_k_values))
        retrieved_ids = [item['id'] for item in retrieved]

        # 计算每个 k 值的结果
        result = {
            'question': question,
            'category': category,
            'is_negative': is_negative,
            'expected_ids': list(expected_ids),
            'retrieved': [],
            'top1_score': retrieved[0].get('score', 0) if retrieved else 0,
        }

        for item in retrieved[:max(top_k_values)]:
            result['retrieved'].append({
                'chunk_id': item['id'],
                'score': round(item.get('score', 0), 4),
                'text_preview': item.get('content', '')[:80]
            })

        # 计算命中情况
        if is_negative:
            # 负例：没有期望答案，记录top-1分数用于阈值分析
            result['recall@5'] = None
            result['recall@10'] = None
            result['hit@5'] = None
            result['hit@10'] = None
            result['first_hit_rank@5'] = None
            result['first_hit_rank@10'] = None
        else:
            # 正例：正常计算召回率
            for k in top_k_values:
                top_k_ids = set(retrieved_ids[:k])
                hits = expected_ids & top_k_ids
                result[f'recall@{k}'] = len(hits) / len(expected_ids) if expected_ids else 0
                result[f'hit@{k}'] = len(hits) > 0

                # 计算首次命中排名（用于 MRR）
                if len(hits) > 0:
                    first_hit_rank = min([retrieved_ids.index(hit_id) + 1
                                         for hit_id in hits
                                         if hit_id in retrieved_ids[:k]])
                    result[f'first_hit_rank@{k}'] = first_hit_rank
                else:
                    result[f'first_hit_rank@{k}'] = None

        results.append(result)

    return results


def print_results(results, top_k_values=[5, 10]):
    """打印评估结果"""
    # 分离正例和负例
    positive_results = [r for r in results if not r['is_negative']]
    negative_results = [r for r in results if r['is_negative']]

    print("=" * 100)
    print("检索评估结果")
    print("=" * 100)
    print()

    # 只打印正例的详细结果（负例放在后面单独分析）
    print(f"=== 正例（有答案的问题）: {len(positive_results)} 个 ===\n")
    for i, result in enumerate(positive_results, 1):
        print(f"【正例 {i}】")
        print(f"问题: {result['question']}")
        print(f"分类: {result['category']}")
        print(f"期望命中: {', '.join(result['expected_ids']) if result['expected_ids'] else '无'}")
        print(f"检索结果 (top-{max(top_k_values)}):")

        for j, item in enumerate(result['retrieved'], 1):
            hit_marker = "✓" if item['chunk_id'] in result['expected_ids'] else " "
            print(f"  {j}. [{hit_marker}] {item['chunk_id']} (分数: {item['score']})")
            print(f"      {item['text_preview']}...")

        # 命中情况
        for k in top_k_values:
            if result[f'hit@{k}'] is not None:
                hit_status = "✓ 命中" if result[f'hit@{k}'] else "✗ 未命中"
                rank = result[f'first_hit_rank@{k}']
                rank_str = f"排名 {rank}" if rank else "未命中"
                recall = result[f'recall@{k}']
                print(f"  Recall@{k}: {recall:.2%} | {hit_status} | {rank_str}")

        print()

    # 打印负例样本
    print("=" * 100)
    print(f"=== 负例（库里没答案的问题）: {len(negative_results)} 个 ===")
    print("=" * 100)
    print()

    for i, result in enumerate(negative_results, 1):
        print(f"【负例 {i}】")
        print(f"问题: {result['question']}")
        print(f"Top-1 结果:")
        if result['retrieved']:
            top1 = result['retrieved'][0]
            print(f"  chunk_id: {top1['chunk_id']}")
            print(f"  分数: {top1['score']}")
            print(f"  内容预览: {top1['text_preview']}...")
        print()

    # 总体统计（只统计正例）
    print("=" * 100)
    print("正例总体统计")
    print("=" * 100)

    for k in top_k_values:
        total_cases = len(positive_results)
        hit_count = sum(1 for r in positive_results if r[f'hit@{k}'])
        avg_recall = sum(r[f'recall@{k}'] for r in positive_results if r[f'recall@{k}'] is not None) / total_cases if total_cases > 0 else 0

        # 计算 MRR
        ranks = [r[f'first_hit_rank@{k}'] for r in positive_results if r[f'first_hit_rank@{k}'] is not None]
        mrr = sum(1.0 / rank for rank in ranks) / total_cases if ranks and total_cases > 0 else 0

        print(f"\nTop-{k} 结果:")
        print(f"  总案例数: {total_cases}")
        print(f"  命中案例数: {hit_count}")
        print(f"  命中率 (Hit Rate): {hit_count / total_cases:.2%}" if total_cases > 0 else "  命中率: N/A")
        print(f"  平均 Recall@{k}: {avg_recall:.2%}")
        print(f"  MRR (Mean Reciprocal Rank): {mrr:.4f}")

        if ranks:
            avg_rank = sum(ranks) / len(ranks)
            print(f"  平均命中排名: {avg_rank:.2f}")

    # 按分类统计（只统计正例）
    print("\n" + "=" * 100)
    print("正例分类统计 (Recall@5)")
    print("=" * 100)

    categories = {}
    for result in positive_results:
        cat = result['category']
        if cat not in categories:
            categories[cat] = []
        if result['recall@5'] is not None:
            categories[cat].append(result['recall@5'])

    for cat, recalls in sorted(categories.items()):
        if recalls:
            avg_recall = sum(recalls) / len(recalls)
            print(f"  {cat}: {avg_recall:.2%} ({len(recalls)} 个案例)")

    print()


def analyze_score_distribution(results):
    """分析正例和负例的分数分布"""
    positive_scores = []
    negative_scores = []

    for result in results:
        if result['is_negative']:
            negative_scores.append(result['top1_score'])
        else:
            # 正例：取命中的chunk的分数
            if result.get('hit@5'):
                for item in result['retrieved']:
                    if item['chunk_id'] in result['expected_ids']:
                        positive_scores.append(item['score'])
                        break

    return positive_scores, negative_scores


def recommend_threshold(positive_scores, negative_scores):
    """推荐相关性阈值"""
    import statistics

    if not positive_scores or not negative_scores:
        return None, None

    pos_min = min(positive_scores)
    pos_max = max(positive_scores)
    pos_mean = statistics.mean(positive_scores)
    pos_median = statistics.median(positive_scores)

    neg_min = min(negative_scores)
    neg_max = max(negative_scores)
    neg_mean = statistics.mean(negative_scores)
    neg_median = statistics.median(negative_scores)

    # 推荐阈值：负例最高分和正例最低分之间
    # 策略1: 取中点
    threshold_midpoint = (pos_min + neg_max) / 2

    # 策略2: 偏向召回（接近负例最高分）
    threshold_recall_biased = neg_max + (pos_min - neg_max) * 0.2

    # 策略3: 偏向精确（接近正例最低分）
    threshold_precision_biased = neg_max + (pos_min - neg_max) * 0.8

    return {
        'positive': {
            'min': round(pos_min, 4),
            'max': round(pos_max, 4),
            'mean': round(pos_mean, 4),
            'median': round(pos_median, 4),
            'count': len(positive_scores)
        },
        'negative': {
            'min': round(neg_min, 4),
            'max': round(neg_max, 4),
            'mean': round(neg_mean, 4),
            'median': round(neg_median, 4),
            'count': len(negative_scores)
        },
        'gap': round(pos_min - neg_max, 4),
        'recommended_thresholds': {
            'midpoint': round(threshold_midpoint, 4),
            'recall_biased': round(threshold_recall_biased, 4),
            'precision_biased': round(threshold_precision_biased, 4)
        }
    }


def evaluate_with_threshold(results, threshold):
    """使用阈值重新评估"""
    positive_results = [r for r in results if not r['is_negative']]
    negative_results = [r for r in results if r['is_negative']]

    # 正例：检查是否有命中的chunk被阈值误杀
    false_negatives = 0  # 应该召回但被阈值拦住
    true_positives = 0   # 正确召回

    for result in positive_results:
        hit_above_threshold = False
        for item in result['retrieved']:
            if item['chunk_id'] in result['expected_ids'] and item['score'] >= threshold:
                hit_above_threshold = True
                break

        if result.get('hit@5'):
            if hit_above_threshold:
                true_positives += 1
            else:
                false_negatives += 1

    # 负例：检查top-1是否被阈值正确拦截
    true_negatives = 0   # 正确拦截
    false_positives = 0  # 误报（不应返回却返回了）

    for result in negative_results:
        if result['top1_score'] < threshold:
            true_negatives += 1
        else:
            false_positives += 1

    return {
        'threshold': threshold,
        'positive': {
            'total': len(positive_results),
            'true_positives': true_positives,
            'false_negatives': false_negatives,
            'recall': true_positives / len(positive_results) if positive_results else 0
        },
        'negative': {
            'total': len(negative_results),
            'true_negatives': true_negatives,
            'false_positives': false_positives,
            'block_rate': true_negatives / len(negative_results) if negative_results else 0
        }
    }


def main():
    # 加载评估集
    eval_path = Path(__file__).parent / "retrieval_eval.jsonl"
    print(f"加载评估集: {eval_path}")
    eval_cases = load_eval_set(eval_path)
    positive_count = sum(1 for c in eval_cases if c.get('category') != 'negative')
    negative_count = sum(1 for c in eval_cases if c.get('category') == 'negative')
    print(f"共 {len(eval_cases)} 个评估案例 (正例: {positive_count}, 负例: {negative_count})\n")

    # 执行评估
    print("开始评估检索性能...")
    results = evaluate_retrieval(eval_cases, top_k_values=[5, 10])

    # 打印结果
    print_results(results, top_k_values=[5, 10])

    # 分数分布分析
    print("=" * 100)
    print("分数分布分析")
    print("=" * 100)
    print()

    positive_scores, negative_scores = analyze_score_distribution(results)

    print(f"正例命中分数分布:")
    print(f"  样本数: {len(positive_scores)}")
    if positive_scores:
        import statistics
        print(f"  最小值: {min(positive_scores):.4f}")
        print(f"  最大值: {max(positive_scores):.4f}")
        print(f"  平均值: {statistics.mean(positive_scores):.4f}")
        print(f"  中位数: {statistics.median(positive_scores):.4f}")

    print(f"\n负例 Top-1 分数分布:")
    print(f"  样本数: {len(negative_scores)}")
    if negative_scores:
        import statistics
        print(f"  最小值: {min(negative_scores):.4f}")
        print(f"  最大值: {max(negative_scores):.4f}")
        print(f"  平均值: {statistics.mean(negative_scores):.4f}")
        print(f"  中位数: {statistics.median(negative_scores):.4f}")

    # 推荐阈值
    if positive_scores and negative_scores:
        print("\n" + "=" * 100)
        print("阈值推荐")
        print("=" * 100)
        print()

        threshold_info = recommend_threshold(positive_scores, negative_scores)

        print(f"正例分数范围: [{threshold_info['positive']['min']}, {threshold_info['positive']['max']}]")
        print(f"负例分数范围: [{threshold_info['negative']['min']}, {threshold_info['negative']['max']}]")
        print(f"分数间隙 (Gap): {threshold_info['gap']:.4f}")

        if threshold_info['gap'] > 0:
            print(f"\n✓ 正例和负例分数可分离（有间隙）")
        else:
            print(f"\n⚠️  正例和负例分数有重叠（负例最高分 > 正例最低分）")

        print(f"\n推荐阈值:")
        thresholds = threshold_info['recommended_thresholds']
        print(f"  1. 中点策略: {thresholds['midpoint']:.4f}")
        print(f"  2. 偏向召回: {thresholds['recall_biased']:.4f} (宁可误报，不要漏报)")
        print(f"  3. 偏向精确: {thresholds['precision_biased']:.4f} (宁可漏报，不要误报)")

        # 测试每个阈值
        print("\n" + "=" * 100)
        print("阈值验证")
        print("=" * 100)

        for name, threshold in [
            ("中点策略", thresholds['midpoint']),
            ("偏向召回", thresholds['recall_biased']),
            ("偏向精确", thresholds['precision_biased'])
        ]:
            print(f"\n使用阈值: {threshold:.4f} ({name})")
            eval_result = evaluate_with_threshold(results, threshold)

            pos = eval_result['positive']
            neg = eval_result['negative']

            print(f"  正例:")
            print(f"    总数: {pos['total']}")
            print(f"    正确召回: {pos['true_positives']}")
            print(f"    被误杀: {pos['false_negatives']}")
            print(f"    召回率: {pos['recall']:.2%}")

            print(f"  负例:")
            print(f"    总数: {neg['total']}")
            print(f"    正确拦截: {neg['true_negatives']}")
            print(f"    误报: {neg['false_positives']}")
            print(f"    拦截率: {neg['block_rate']:.2%}")

    # 保存结果
    output_path = Path(__file__).parent / "retrieval_eval_results.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存到: {output_path}")


if __name__ == "__main__":
    main()


