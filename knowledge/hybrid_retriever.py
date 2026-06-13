"""BM25 关键词检索 + RRF 融合

BM25 (Best Matching 25):
- 工业级关键词检索算法（Elasticsearch 默认算法）
- 考虑词频(TF) + 逆文档频率(IDF) + 文档长度归一化
- 对精确词、型号、订单号召回准确

RRF (Reciprocal Rank Fusion):
- 倒数排名融合，合并多路检索结果
- 公式: score = Σ 1/(k + rank)，k 默认 60
- 不依赖各路分数的绝对值，只看排名，鲁棒性强
"""
import math
import re
import logging
from typing import List, Dict, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


def tokenize(text: str) -> List[str]:
    """分词（中英文混合）

    策略:
    - 英文/数字: 按空格和标点分词，保留完整词（如 iPhone15）
    - 中文: 按字符 + 2-gram（兼顾单字和词组）
    """
    text = text.lower()

    # 提取英文单词和数字（保留型号如 iphone15）
    english_tokens = re.findall(r'[a-z0-9]+', text)

    # 提取中文字符
    chinese_chars = re.findall(r'[一-鿿]', text)

    # 中文 2-gram
    chinese_bigrams = []
    for i in range(len(chinese_chars) - 1):
        chinese_bigrams.append(chinese_chars[i] + chinese_chars[i + 1])

    return english_tokens + chinese_chars + chinese_bigrams


class BM25Retriever:
    """BM25 关键词检索器

    参数:
    - k1: 词频饱和参数（默认 1.5，控制 TF 的影响）
    - b: 文档长度归一化参数（默认 0.75）
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        # 索引数据
        self.documents = []          # [{"id": str, "tokens": [...], "content": str, "metadata": {}}]
        self.doc_freqs = defaultdict(int)  # token -> 出现该 token 的文档数
        self.idf = {}                # token -> IDF 值
        self.avg_doc_len = 0         # 平均文档长度
        self.total_docs = 0

    def build_index(self, documents: List[Dict[str, Any]]):
        """构建 BM25 索引

        Args:
            documents: [{"id": str, "content": str, "metadata": {}}, ...]
        """
        self.documents = []
        self.doc_freqs = defaultdict(int)

        total_len = 0
        for doc in documents:
            tokens = tokenize(doc["content"])
            self.documents.append({
                "id": doc["id"],
                "tokens": tokens,
                "content": doc["content"],
                "metadata": doc.get("metadata", {})
            })
            total_len += len(tokens)

            # 统计文档频率（每个 token 在多少文档中出现）
            for token in set(tokens):
                self.doc_freqs[token] += 1

        self.total_docs = len(documents)
        self.avg_doc_len = total_len / self.total_docs if self.total_docs > 0 else 0

        # 计算 IDF
        for token, freq in self.doc_freqs.items():
            # IDF = log((N - df + 0.5) / (df + 0.5) + 1)
            self.idf[token] = math.log((self.total_docs - freq + 0.5) / (freq + 0.5) + 1)

        logger.info(f"✓ BM25 索引构建完成: {self.total_docs} 文档, {len(self.doc_freqs)} 唯一 token")

    def search(
        self,
        query: str,
        top_k: int = 20,
        filter_dict: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """BM25 检索"""
        if not self.documents:
            return []

        query_tokens = tokenize(query)
        query_token_set = set(query_tokens)

        results = []
        for doc in self.documents:
            # 元数据过滤
            if filter_dict and not self._match_filter(doc["metadata"], filter_dict):
                continue

            # 计算 BM25 分数
            score = self._bm25_score(query_token_set, doc["tokens"])

            if score > 0:
                results.append({
                    "id": doc["id"],
                    "score": score,
                    "content": doc["content"],
                    "metadata": doc["metadata"]
                })

        # 归一化分数到 0-1
        if results:
            max_score = max(r["score"] for r in results)
            if max_score > 0:
                for r in results:
                    r["score"] = r["score"] / max_score

        # 排序并返回 top-k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _bm25_score(self, query_tokens: set, doc_tokens: List[str]) -> float:
        """计算单个文档的 BM25 分数"""
        doc_len = len(doc_tokens)
        doc_token_counts = defaultdict(int)
        for token in doc_tokens:
            doc_token_counts[token] += 1

        score = 0.0
        for token in query_tokens:
            if token not in doc_token_counts:
                continue

            tf = doc_token_counts[token]
            idf = self.idf.get(token, 0)

            # BM25 公式
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len)

            score += idf * (numerator / denominator)

        return score

    def _match_filter(self, metadata: Dict, filter_dict: Dict) -> bool:
        """检查元数据是否匹配过滤条件"""
        for key, value in filter_dict.items():
            if key not in metadata:
                return False
            if metadata[key] != value:
                return False
        return True


def reciprocal_rank_fusion(
    result_lists: List[List[Dict[str, Any]]],
    k: int = 60,
    id_key: str = "id"
) -> List[Dict[str, Any]]:
    """RRF (倒数排名融合)

    合并多路检索结果，公式:
        RRF_score(d) = Σ 1/(k + rank_i(d))

    其中 rank_i(d) 是文档 d 在第 i 路结果中的排名（从1开始）

    Args:
        result_lists: 多路检索结果列表 [[result1, result2...], [...], ...]
        k: RRF 常数（默认 60，经验值）
        id_key: 结果中标识文档的键

    Returns:
        融合后的结果列表（按 RRF 分数排序）
    """
    # 累积每个文档的 RRF 分数
    rrf_scores = defaultdict(float)
    doc_data = {}  # id -> 完整文档数据

    for result_list in result_lists:
        for rank, result in enumerate(result_list, start=1):
            doc_id = result[id_key]
            rrf_scores[doc_id] += 1.0 / (k + rank)

            # 保存文档数据（用第一次出现的）
            if doc_id not in doc_data:
                doc_data[doc_id] = result

    # 构建融合结果
    fused_results = []
    for doc_id, rrf_score in rrf_scores.items():
        result = doc_data[doc_id].copy()
        result["rrf_score"] = rrf_score
        result["score"] = rrf_score  # 用 RRF 分数作为最终分数
        fused_results.append(result)

    # 按 RRF 分数排序
    fused_results.sort(key=lambda x: x["rrf_score"], reverse=True)

    logger.info(f"✓ RRF 融合: {len(result_lists)} 路结果 -> {len(fused_results)} 条去重结果")

    return fused_results
