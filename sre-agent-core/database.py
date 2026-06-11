import os
from datetime import datetime
from typing import Generator
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from config import settings

# Initialize DB connection engine
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    github_id = Column(Integer, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    access_token = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class MonitoredRepo(Base):
    __tablename__ = "monitored_repos"
    
    id = Column(Integer, primary_key=True, index=True)
    github_id = Column(Integer, unique=True, index=True)
    name = Column(String, index=True)  # full name: owner/repo
    branch = Column(String, default="master")
    webhook_connected = Column(Boolean, default=True)
    healing_enabled = Column(Boolean, default=True)
    user_id = Column(Integer, index=True)  # owner user id (primary key)
    created_at = Column(DateTime, default=datetime.utcnow)

class HealingRunRecord(Base):
    __tablename__ = "healing_runs"
    
    id = Column(String, primary_key=True, index=True)  # workflow run_id or simulation ID
    job_id = Column(String, nullable=True)
    job_name = Column(String)
    repo = Column(String, index=True)
    branch = Column(String)
    timestamp = Column(String)  # formatted timestamp: YYYY-MM-DD HH:MM:SS
    status = Column(String)     # diagnosing, healing, resolved, failed
    explanation = Column(Text)
    modifications = Column(JSON, default=list)  # list of modification dicts
    pr_url = Column(String, nullable=True)
    user_id = Column(Integer, index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    """Initializes tables in PostgreSQL database."""
    Base.metadata.create_all(bind=engine)

def get_db() -> Generator[Session, None, None]:
    """Dependency provider for FastAPI route handlers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
