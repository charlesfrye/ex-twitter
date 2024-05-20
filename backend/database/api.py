from datetime import datetime
import os
from typing import List, Optional

import fastapi
import modal
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import common


image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "asyncpg==0.29.0", "sqlalchemy[asyncio]==2.0.30"
)


app = modal.App(
    "db-client",
    image=image,
    secrets=[modal.Secret.from_name("pgsql-secret")],
)


@app.function(
    keep_warm=1,
    allow_concurrent_inputs=1000,
    concurrency_limit=1,
    mounts=[common.mount],
)
@modal.asgi_app()
def api() -> FastAPI:
    """API for accessing the Twitter '95 database.

    The primary routes for the bot client and the frontend are:
        - GET /timeline/, which returns fake-time-limited tweets based on user follows
        - GET /posts/, which returns fake-time-limited tweets from a specific user
        - GET /profile/, which returns the user and their bio
        - POST /tweet/, which creates a new tweet

    The remaining routes are lower level (e.g. retrieving all tweets).
    """
    from sqlalchemy import and_, asc, delete, desc, or_
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.future import select
    from sqlalchemy.orm import sessionmaker

    import common.models as models

    api = FastAPI(
        title="twitter95",
        summary="What if Twitter was made in 1995?",
        version="0.1.0",
        docs_url="/",
        redoc_url=None,
    )

    def connect():
        user = os.environ["PGUSER"]
        password = os.environ["PGPASSWORD"]
        host = os.environ["PGHOST"]
        port = os.environ["PGPORT"]
        database = os.environ["PGDATABASE"]

        connection_string = (
            f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
        )

        engine = create_async_engine(
            connection_string,
            isolation_level="READ COMMITTED",  # default and lowest level in pgSQL
            echo=True,  # log SQL as it is emitted
        )

        return engine

    engine = connect()

    new_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.get("/timeline/", response_model=List[models.pydantic.TweetRead])
    async def read_timeline(
        fake_time: datetime, user_id: int, limit: int = 10, ascending: bool = False
    ):
        """Read the timeline at a specific (fake) time."""
        sort = asc if ascending else desc
        async with new_session() as db:
            followed_users = select(
                models.sql.followers_association.c.followed_id
            ).where(models.sql.followers_association.c.follower_id == user_id)

            # TODO: enrich with info about authors of tweets
            result = await db.execute(
                select(models.sql.Tweet)  # author here?
                .where(models.sql.Tweet.author_id.in_(followed_users))
                .filter(
                    and_(
                        or_(
                            models.sql.Tweet.fake_time <= fake_time,
                            models.sql.Tweet.fake_time == None,  # noqa: E711
                        ),
                    )
                )
                .order_by(
                    sort(models.sql.Tweet.fake_time), sort(models.sql.Tweet.tweet_id)
                )
                .limit(limit)
            )

            tweets = result.scalars()

        return list(tweets)

    @api.get("/posts/", response_model=List[models.pydantic.TweetRead])
    async def read_posts(
        fake_time: datetime, user_id: int, limit: int = 10, ascending: bool = False
    ):
        """Read a specific user's tweets at a specific (fake) time."""
        sort = asc if ascending else desc
        async with new_session() as db:
            results = await db.execute(
                select(models.sql.Tweet)
                .filter(
                    and_(
                        or_(
                            models.sql.Tweet.fake_time <= fake_time,
                            models.sql.Tweet.fake_time == None,  # noqa: E711
                        ),
                    ),
                    models.sql.Tweet.author_id == user_id,
                )
                .order_by(
                    sort(models.sql.Tweet.fake_time), sort(models.sql.Tweet.tweet_id)
                )
                .limit(limit)
            )
            posts = results.scalars()

        return list(posts)

    @api.get("/profile/{user_id}/", response_model=models.pydantic.ProfileRead)
    async def read_profile(user_id: int):
        """Read the profile information of a user."""
        async with new_session() as db:
            result = await db.execute(
                select(models.sql.User).filter_by(user_id=user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                return FastAPI.HTTPException(status_code=404, detail="User not found")
            bio = await user.awaitable_attrs.bio

        if bio is None:
            return models.pydantic.ProfileRead(
                user=user,
                bio={"user_id": user_id},
            )

        return {"user": user, "bio": bio}

    @api.get("/tweets/", response_model=List[models.pydantic.TweetRead])
    async def read_tweets(limit=10, ascending: bool = False):
        """Read multiple tweets."""
        sort = asc if ascending else desc
        async with new_session() as db:
            result = await db.execute(
                select(models.sql.Tweet)
                .order_by(
                    sort(models.sql.Tweet.fake_time), sort(models.sql.Tweet.tweet_id)
                )
                .limit(limit)
            )
            tweets = result.scalars()
        return list(tweets)

    @api.post("/users/", response_model=models.pydantic.UserRead)
    async def create_user(user: models.pydantic.UserCreate):
        """Create a new User."""
        async with new_session() as db:
            user = models.sql.User(**user.dict())
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # TODO: from_orm
        user = models.pydantic.UserRead(**user.__dict__)

        return user

    @api.get("/users/", response_model=List[models.pydantic.UserRead])
    async def read_users(ascending: bool = False, limit: int = 10):
        """Read multiple users."""
        async with new_session() as db:
            users = await db.scalars(
                select(models.sql.User)
                .order_by(
                    models.sql.User.user_id
                    if ascending
                    else desc(models.sql.User.user_id)
                )
                .limit(limit)
            )

        return list(users)

    @api.get("/users/{user_id}/")
    async def read_user(user_id: int) -> models.pydantic.UserRead:
        """Read a specific user"""
        async with new_session() as db:
            result = await db.execute(
                select(models.sql.User).filter_by(user_id=user_id)
            )
            user = result.scalar_one_or_none()
        if user is None:
            raise fastapi.HTTPException(
                status_code=404, detail=f"User {user_id} not found"
            )
        return user

    @api.delete("/users/{user_id}/")
    async def delete_user(user_id: int):
        """Delete a user and all their data."""
        async with new_session() as db:
            result = await db.execute(
                select(models.sql.User).filter_by(user_id=user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise fastapi.HTTPException(
                    status_code=404, detail=f"User {user_id} not found"
                )

            # remove tweets
            await db.execute(
                delete(models.sql.Tweet).where(models.sql.Tweet.author_id == user_id)
            )

            # remove following edges
            await db.execute(
                delete(models.sql.followers_association).where(
                    models.sql.followers_association.c.follower_id == user_id
                )
            )

            # remove followed edges
            await db.execute(
                delete(models.sql.followers_association).where(
                    models.sql.followers_association.c.followed_id == user_id
                )
            )

            # remove the user
            await db.delete(user)

            # Commit transaction
            await db.commit()

    @api.get("/users/{user_id}/tweets/", response_model=List[models.pydantic.TweetRead])
    async def read_user_tweets(user_id: int, limit=10):
        """Read all tweets by a user."""
        async with new_session() as db:
            result = await db.execute(
                select(models.sql.User).filter_by(user_id=user_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise fastapi.HTTPException(
                    status_code=404, detail=f"User {user_id} not found"
                )

            result = await db.scalars(
                select(models.sql.Tweet)
                .filter_by(author_id=user_id)
                .order_by(
                    desc(models.sql.Tweet.fake_time), desc(models.sql.Tweet.tweet_id)
                )
                .limit(limit)
            )
            tweets = result.all()

        return list(tweets)

    @api.get("/names/{user_name}/")
    async def read_user_by_name(user_name: str) -> Optional[models.pydantic.UserRead]:
        """Read a specific user by their user_name."""
        async with new_session() as db:
            result = await db.execute(
                select(models.sql.User).filter_by(user_name=user_name)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise fastapi.HTTPException(
                    status_code=404, detail=f"User {user_name} not found"
                )
        return user

    @api.post("/tweet/", response_model=models.pydantic.TweetRead)
    async def create_tweet(tweet: models.pydantic.TweetCreate):
        """Create a new tweet."""
        tweet = models.sql.Tweet(**tweet.dict())

        async with new_session() as db:
            db.add(tweet)
            await db.commit()
            await db.refresh(tweet)

        return tweet

    return api
