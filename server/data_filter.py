import datetime
import os

from collections import defaultdict

from atproto import models

from server.logger import logger
from server.database import db, Post

from dotenv import load_dotenv


load_dotenv()


def is_archive_post(record):
    # Sometimes users will import old posts from Twitter/X which con flood a feed with
    # old posts. Unfortunately, the only way to test for this is to look an old
    # created_at date. However, there are other reasons why a post might have an old
    # date, such as firehose or firehose consumer outages. It is up to you, the feed
    # creator to weigh the pros and cons, amd and optionally include this function in
    # your filter conditions, and adjust the threshold to your liking.
    #
    # See https://github.com/MarshalX/bluesky-feed-generator/pull/21

    archived_threshold = datetime.timedelta(days=1)
    created_at = datetime.datetime.fromisoformat(record.created_at)
    now = datetime.datetime.now(datetime.UTC)

    return now - created_at > archived_threshold


def is_enabled(setting: str|None) -> bool:
    if setting is None:
        return False
    return setting.lower() in ["true", "yes", "y"]


def ignore_post(record):
    if is_enabled(os.environ.get("IGNORE_ARCHIVED_POSTS")) and is_archive_post(record):
        return True
    if is_enabled(os.environ.get("IGNORE_REPLY_POSTS")) and record.reply:
        return True
    return False


def operations_callback(ops: defaultdict) -> None:
    # Here we can filter, process, run ML classification, etc.
    # After our feed alg we can save posts into our DB
    # Also, we should process deleted posts to remove them from our DB and keep it in sync

    # for example, let's create our custom feed that will contain all posts that contains alf related text

    posts_to_create = []
    for created_post in ops[models.ids.AppBskyFeedPost]['created']:
        author = created_post['author']
        record = created_post['record']

        # print all texts just as demo that data stream works
        post_with_images = isinstance(record.embed, models.AppBskyEmbedImages.Main)
        inlined_text = record.text.replace('\n', ' ')
        logger.debug(
            f'NEW POST '
            f'[CREATED_AT={record.created_at}]'
            f'[AUTHOR={author}]'
            f'[WITH_IMAGE={post_with_images}]'
            f': {inlined_text}'
        )

        # only python-related posts
        if 'python' in record.text.lower() and not ignore_post(record):
            reply_root = reply_parent = None
            if record.reply:
                reply_root = record.reply.root.uri
                reply_parent = record.reply.parent.uri

            post_dict = {
                'uri': created_post['uri'],
                'cid': created_post['cid'],
                'reply_parent': reply_parent,
                'reply_root': reply_root,
            }
            posts_to_create.append(post_dict)

    posts_to_delete = ops[models.ids.AppBskyFeedPost]['deleted']
    if posts_to_delete:
        post_uris_to_delete = [post['uri'] for post in posts_to_delete]
        Post.delete().where(Post.uri.in_(post_uris_to_delete))
        logger.debug(f'Deleted from feed: {len(post_uris_to_delete)}')

    if posts_to_create:
        with db.atomic():
            for post_dict in posts_to_create:
                Post.create(**post_dict)
        logger.debug(f'Added to feed: {len(posts_to_create)}')
