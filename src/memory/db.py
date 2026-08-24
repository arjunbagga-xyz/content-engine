import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from src.core.config import config

Base = declarative_base()

class Character(Base):
    __tablename__ = "characters"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    status = Column(String, default="active")  # active, inactive
    role = Column(String, nullable=False)
    personality = Column(Text, nullable=False)
    visual_keywords = Column(Text, nullable=False)
    voice = Column(String, nullable=False)
    themes = Column(Text, nullable=False)  # JSON-encoded string
    reel_style = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    posts = relationship("ContentPost", back_populates="character")
    events = relationship("NarrativeEvent", back_populates="character")
    arcs = relationship("ArcSummary", back_populates="character")

class NarrativeEvent(Base):
    __tablename__ = "narrative_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    event_description = Column(Text, nullable=False)
    importance = Column(Integer, default=5)  # 1-10 scale
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    character = relationship("Character", back_populates="events")

class ArcSummary(Base):
    __tablename__ = "arc_summaries"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    summary_text = Column(Text, nullable=False)
    week_start = Column(DateTime, nullable=False)
    week_end = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    character = relationship("Character", back_populates="arcs")

class ContentPost(Base):
    __tablename__ = "content_queue"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(String, ForeignKey("characters.id"), nullable=False)
    platform = Column(String, nullable=False)  # instagram, x
    post_type = Column(String, nullable=False)  # static, carousel, tweet, reel, thread
    state = Column(String, default="planned")  # planned, scripted, media_ready, staged, published, failed, held
    scheduled_time = Column(DateTime, nullable=False)
    actual_posted_time = Column(DateTime, nullable=True)
    caption = Column(Text, nullable=True)
    script = Column(Text, nullable=True)
    media_path = Column(String, nullable=True)  # Path to generated image/video
    image_prompt = Column(Text, nullable=True)
    platform_post_id = Column(String, nullable=True)  # Store IG/X post ID after publishing
    media_type = Column(String, nullable=True)  # photo, video, carousel, text
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    character = relationship("Character", back_populates="posts")

# Setup Database
engine = create_engine(f"sqlite:///{config.SQLITE_DB_PATH}", echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
