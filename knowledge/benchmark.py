"""RAG 检索性能基准测试

测试场景:
1. 生成 100万条假知识
2. 灌入索引（FAISS HNSW vs SQLite 暴力）
3. 随机跑 100 条 query
4. 统计: 平均延迟、P95延迟、召回数、命中率

运行: python knowledge/benchmark.py
"""
import sys
import os
import time
import random
import numpy as np
from typing import List, Dict

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.vector_index import FaissHNSWIndex, SqliteBruteForceIndex


def generate_fake_docs(n: int = 1000000) -> List[Dict]:
    """生成假知识（可复现）

    策略: 用固定种子生成随机文本，节省内存
    """
    print(f"生成 {n:,} 条假知识...")
    random.seed(42)
    np.random.seed(42)

    # 预设词库（客服场景）
    nouns = ["订单", "退货", "换货", "保修", "物流", "会员", "优惠券", "商品", "售后", "客服"]
    verbs = ["查询", "申请", "办理", "取消", "修改", "确认", "处理", "提交", "审核", "完成"]
    adjectives = ["快速", "便捷", "优质", "专业", "及时", "满意", "放心", "贴心", "周到", "高效"]

    docs = []
    for i in range(n):
        # 生成随机文本（10-30字）
        length = random.randint(10, 30)
        words = [random.choice(nouns + verbs + adjectives) for _ in range(length)]
        text = "".join(words)

        # 生成随机向量（512维，归一化）
        vector = np.random.randn(512).astype(np.float32)
        vector = vector / np.linalg.norm(vector)

        docs.append({
            "id": f"doc_{i}",
            "text": text,
            "vector": vector,
            "metadata": {
                "category": random.choice(["退货", "换货", "保修", "会员"]),
                "source": f"FAQ_{i % 100}"
            }
        })

        if (i + 1) % 100000 == 0:
            print(f"  已生成 {i+1:,} 条")

    return docs


def build_index_benchmark(docs: List[Dict], backend: str):
    """构建索引并计时"""
    print(f"\n=== 构建 {backend} 索引 ===")

    if backend == "faiss_hnsw":
        index = FaissHNSWIndex(dimension=512, M=32, ef_construction=200, ef_search=100)
    elif backend == "sqlite_bruteforce":
        index = SqliteBruteForceIndex(dimension=512)
    else:
        raise ValueError(f"未知后端: {backend}")

    # 批量添加
    start = time.time()
    batch_size = 10000

    for i in range(0, len(docs), batch_size):
        batch = docs[i:i+batch_size]
        vectors = np.array([d["vector"] for d in batch])
        ids = [d["id"] for d in batch]
        metadatas = [d["metadata"] for d in batch]

        index.add(vectors, ids, metadatas)

        if (i + batch_size) % 100000 == 0:
            elapsed = time.time() - start
            print(f"  已索引 {i+batch_size:,} 条，耗时 {elapsed:.1f}秒")

    total_time = time.time() - start
    print(f"✓ 索引构建完成: {len(docs):,} 条，耗时 {total_time:.1f}秒")

    return index


def search_benchmark(index, docs: List[Dict], num_queries: int = 100):
    """检索基准测试"""
    print(f"\n=== 检索性能测试（{num_queries} 条查询）===")

    # 随机采样 query（从 docs 中选，确保有召回）
    random.seed(123)
    query_docs = random.sample(docs, num_queries)

    latencies = []
    recall_counts = []

    for i, query_doc in enumerate(query_docs):
        query_vector = query_doc["vector"]

        # 检索
        start = time.time()
        results = index.search(query_vector, top_k=20)
        latency = (time.time() - start) * 1000  # 毫秒

        latencies.append(latency)
        recall_counts.append(len(results))

        if (i + 1) % 20 == 0:
            print(f"  已完成 {i+1}/{num_queries} 条查询")

    # 统计
    avg_latency = np.mean(latencies)
    p50_latency = np.percentile(latencies, 50)
    p95_latency = np.percentile(latencies, 95)
    p99_latency = np.percentile(latencies, 99)
    max_latency = np.max(latencies)
    avg_recall = np.mean(recall_counts)

    print(f"\n【结果】")
    print(f"  平均延迟: {avg_latency:.2f} ms")
    print(f"  P50 延迟: {p50_latency:.2f} ms")
    print(f"  P95 延迟: {p95_latency:.2f} ms")
    print(f"  P99 延迟: {p99_latency:.2f} ms")
    print(f"  最大延迟: {max_latency:.2f} ms")
    print(f"  平均召回数: {avg_recall:.1f} 条")

    return {
        "avg_latency": avg_latency,
        "p95_latency": p95_latency,
        "p99_latency": p99_latency,
        "avg_recall": avg_recall
    }


def main():
    print("=" * 60)
    print("RAG 检索性能基准测试")
    print("=" * 60)

    # 可调参数
    num_docs = int(os.getenv("BENCHMARK_DOCS", "10000"))  # 默认1万条（百万太慢，测试用1万）
    num_queries = int(os.getenv("BENCHMARK_QUERIES", "100"))

    print(f"\n配置: {num_docs:,} 文档, {num_queries} 查询")
    print("（提示: 设置环境变量 BENCHMARK_DOCS=1000000 测试百万级）\n")

    # 生成数据
    docs = generate_fake_docs(num_docs)

    # 测试 FAISS HNSW
    try:
        print("\n" + "=" * 60)
        print("【测试 1/2】FAISS HNSW 索引")
        print("=" * 60)
        faiss_index = build_index_benchmark(docs, "faiss_hnsw")
        faiss_stats = search_benchmark(faiss_index, docs, num_queries)
    except Exception as e:
        print(f"FAISS 测试失败: {e}")
        faiss_stats = None

    # 对比：暴力检索（仅小数据量）
    if num_docs <= 50000:
        print("\n" + "=" * 60)
        print("【测试 2/2】SQLite 暴力检索（对比基准）")
        print("=" * 60)
        print("注意: 暴力检索 O(N) 复杂度，大数据量会很慢\n")

        try:
            # 暴力检索：直接用 Python 实现，不写入 SQLite
            print("构建暴力检索索引（内存）...")
            start = time.time()
            vectors_array = np.array([d["vector"] for d in docs])
            print(f"✓ 索引构建完成: {len(docs):,} 条，耗时 {time.time()-start:.1f}秒")

            print(f"\n=== 检索性能测试（{num_queries} 条查询）===")
            random.seed(123)
            query_docs = random.sample(docs, num_queries)
            latencies = []

            for i, query_doc in enumerate(query_docs):
                query_vector = query_doc["vector"]

                start = time.time()
                # 暴力点积
                scores = np.dot(vectors_array, query_vector)
                top_indices = np.argsort(scores)[-20:][::-1]
                latency = (time.time() - start) * 1000

                latencies.append(latency)

                if (i + 1) % 20 == 0:
                    print(f"  已完成 {i+1}/{num_queries} 条查询")

            brute_stats = {
                "avg_latency": np.mean(latencies),
                "p95_latency": np.percentile(latencies, 95),
                "p99_latency": np.percentile(latencies, 99)
            }

            print(f"\n【结果】")
            print(f"  平均延迟: {brute_stats['avg_latency']:.2f} ms")
            print(f"  P95 延迟: {brute_stats['p95_latency']:.2f} ms")
            print(f"  P99 延迟: {brute_stats['p99_latency']:.2f} ms")

        except Exception as e:
            print(f"暴力检索测试失败: {e}")
            brute_stats = None
    else:
        print(f"\n数据量 > 5万，跳过暴力检索测试（太慢）")
        brute_stats = None

    # 对比总结
    print("\n" + "=" * 60)
    print("【性能对比总结】")
    print("=" * 60)

    if faiss_stats:
        print(f"FAISS HNSW:")
        print(f"  平均延迟: {faiss_stats['avg_latency']:.2f} ms")
        print(f"  P95 延迟: {faiss_stats['p95_latency']:.2f} ms")
        print(f"  QPS 估算: {1000 / faiss_stats['avg_latency']:.1f}")

    if brute_stats:
        print(f"\n暴力检索:")
        print(f"  平均延迟: {brute_stats['avg_latency']:.2f} ms")
        print(f"  P95 延迟: {brute_stats['p95_latency']:.2f} ms")
        print(f"  QPS 估算: {1000 / brute_stats['avg_latency']:.1f}")

        if faiss_stats:
            speedup = brute_stats['avg_latency'] / faiss_stats['avg_latency']
            print(f"\n✓ FAISS 加速比: {speedup:.1f}x")

    print("\n" + "=" * 60)
    print("【结论】")
    print("=" * 60)
    if faiss_stats and faiss_stats['p95_latency'] < 200:
        print("✓ FAISS HNSW 性能达标（P95 < 200ms）")
    elif faiss_stats:
        print(f"⚠ FAISS HNSW 性能需优化（P95 = {faiss_stats['p95_latency']:.2f}ms）")

    print(f"\n百万级推算:")
    if faiss_stats:
        scale_factor = 1000000 / num_docs
        estimated_p95 = faiss_stats['p95_latency'] * np.log(scale_factor) if scale_factor > 1 else faiss_stats['p95_latency']
        print(f"  100万文档 P95 延迟预估: {estimated_p95:.2f} ms (按 log(N) 增长)")
        if estimated_p95 < 200:
            print("  ✓ 预计达标")
        else:
            print("  ⚠ 需调优 FAISS 参数")


if __name__ == "__main__":
    main()
