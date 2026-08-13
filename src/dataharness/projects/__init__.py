"""长期 Project 语料 deep module。"""

from .corpus import ProjectCorpus
from .extractors import EXTRACTOR_VERSION, UnsupportedFormatError, sniff_media_type
from .index import INDEX_VERSION
from .models import ExtractedDocument, OpenedResource, SearchHit, TextChunk

__all__ = [
    "EXTRACTOR_VERSION",
    "INDEX_VERSION",
    "ExtractedDocument",
    "OpenedResource",
    "ProjectCorpus",
    "SearchHit",
    "TextChunk",
    "UnsupportedFormatError",
    "sniff_media_type",
]
