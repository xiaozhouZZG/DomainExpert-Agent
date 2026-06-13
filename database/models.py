"""数据库模型定义（SQLAlchemy ORM）"""
from sqlalchemy import Column, Integer, String, Text, Float, BLOB, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import logging

logger = logging.getLogger(__name__)

Base = declarative_base()


class Document(Base):
    """文档表"""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(64), unique=True, nullable=False, index=True)
    file_path = Column(String(512), nullable=False)
    title = Column(String(256), nullable=False)
    created_at = Column(String(32), nullable=False)
    # ⚠️ metadata 是 SQLAlchemy 保留名，必须用别名
    doc_metadata = Column("metadata", Text)

    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    """文档分块表"""
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), unique=True, nullable=False, index=True)
    doc_id = Column(String(64), ForeignKey("documents.doc_id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(BLOB)  # 512维 float32 向量，2048 字节

    document = relationship("Document", back_populates="chunks")


class Customer(Base):
    """客户表"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    phone = Column(String(32))
    email = Column(String(128))
    created_at = Column(String(32))


class Order(Base):
    """订单表"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, nullable=False, index=True)
    customer_id = Column(String(64), ForeignKey("customers.customer_id"), nullable=False, index=True)
    product_id = Column(String(64))
    amount = Column(Float)
    status = Column(String(32))  # pending/paid/shipped/completed/cancelled
    created_at = Column(String(32))


class Sale(Base):
    """销售记录表"""
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sale_id = Column(String(64), unique=True, nullable=False, index=True)
    customer_id = Column(String(64), index=True)
    product_id = Column(String(64), index=True)
    amount = Column(Float)
    sale_date = Column(String(16), index=True)  # YYYY-MM-DD
    created_at = Column(String(32))


class Approval(Base):
    """审批表"""
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    approval_id = Column(String(64), unique=True, nullable=False, index=True)
    workflow_type = Column(String(32), nullable=False)  # refund/exchange/expense
    title = Column(String(256))
    content = Column(Text)
    amount = Column(Float)
    status = Column(String(32))  # pending/approved/rejected
    created_at = Column(String(32))


class KGTriple(Base):
    """知识图谱三元组表"""
    __tablename__ = "kg_triples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject = Column(String(256), nullable=False, index=True)
    predicate = Column(String(128), nullable=False)
    object = Column(String(256), nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String(256))  # 来源文档
    created_at = Column(String(32))


class ConversationSession(Base):
    """对话会话表（用于记忆持久化）"""
    __tablename__ = "conversation_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    thread_id = Column(String(64), index=True)
    state_snapshot = Column(Text)  # JSON 序列化的状态
    created_at = Column(String(32))
    updated_at = Column(String(32))


def init_database(db_path: str = "platform.db"):
    """
    初始化数据库（创建所有表）

    参数:
        db_path: 数据库文件路径
    """
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    logger.info(f"数据库初始化完成: {db_path}")

    return engine
