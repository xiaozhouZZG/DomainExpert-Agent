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

        # 执行检索
        retrieved = engine.search(question, top_k=max(top_k_values))
        retrieved_ids = [item['id'] for item in retrieved]

        # 计算每个 k 值的结果
        result = {
            'question': question,
            'category': category,
            'expected_ids': list(expected_ids),
            'retrieved': []
        }

        for item in retrieved[:max(top_k_values)]:
            result['retrieved'].append({
                'chunk_id': item['id'],
                'score': round(item.get('score', 0), 4),
                'text_preview': item.get('content', '')[:80]
            })

        # 计算命中情况
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
    print("=" * 100)
    print("检索评估结果")
    print("=" * 100)
    print()

    # 逐条打印
    for i, result in enumerate(results, 1):
        print(f"【案例 {i}】")
        print(f"问题: {result['question']}")
        print(f"分类: {result['category']}")
        print(f"期望命中: {', '.join(result['expected_ids'])}")
        print(f"检索结果 (top-{max(top_k_values)}):")

        for j, item in enumerate(result['retrieved'], 1):
            hit_marker = "✓" if item['chunk_id'] in result['expected_ids'] else " "
            print(f"  {j}. [{hit_marker}] {item['chunk_id']} (分数: {item['score']})")
            print(f"      {item['text_preview']}...")

        # 命中情况
        for k in top_k_values:
            hit_status = "✓ 命中" if result[f'hit@{k}'] else "✗ 未命中"
            rank = result[f'first_hit_rank@{k}']
            rank_str = f"排名 {rank}" if rank else "未命中"
            print(f"  Recall@{k}: {result[f'recall@{k}']:.2%} | {hit_status} | {rank_str}")

        print()

    # 总体统计
    print("=" * 100)
    print("总体统计")
    print("=" * 100)

    for k in top_k_values:
        total_cases = len(results)
        hit_count = sum(1 for r in results if r[f'hit@{k}'])
        avg_recall = sum(r[f'recall@{k}'] for r in results) / total_cases

        # 计算 MRR
        ranks = [r[f'first_hit_rank@{k}'] for r in results if r[f'first_hit_rank@{k}'] is not None]
        mrr = sum(1.0 / rank for rank in ranks) / total_cases if ranks else 0

        print(f"\nTop-{k} 结果:")
        print(f"  总案例数: {total_cases}")
        print(f"  命中案例数: {hit_count}")
        print(f"  命中率 (Hit Rate): {hit_count / total_cases:.2%}")
        print(f"  平均 Recall@{k}: {avg_recall:.2%}")
        print(f"  MRR (Mean Reciprocal Rank): {mrr:.4f}")

        if ranks:
            avg_rank = sum(ranks) / len(ranks)
            print(f"  平均命中排名: {avg_rank:.2f}")

    # 按分类统计
    print("\n" + "=" * 100)
    print("分类统计 (Recall@5)")
    print("=" * 100)

    categories = {}
    for result in results:
        cat = result['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(result['recall@5'])

    for cat, recalls in sorted(categories.items()):
        avg_recall = sum(recalls) / len(recalls)
        print(f"  {cat}: {avg_recall:.2%} ({len(recalls)} 个案例)")

    print()


def main():
    # 加载评估集
    eval_path = Path(__file__).parent / "retrieval_eval.jsonl"
    print(f"加载评估集: {eval_path}")
    eval_cases = load_eval_set(eval_path)
    print(f"共 {len(eval_cases)} 个评估案例\n")

    # 执行评估
    print("开始评估检索性能...")
    results = evaluate_retrieval(eval_cases, top_k_values=[5, 10])

    # 打印结果
    print_results(results, top_k_values=[5, 10])

    # 保存结果
    output_path = Path(__file__).parent / "retrieval_eval_results.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"详细结果已保存到: {output_path}")


if __name__ == "__main__":
    main()
