from enum import StrEnum


class Sort(StrEnum):
    Sort_relevance = ""
    Sort_newest = "release"
    Sort_most_liked = "likes"
    Sort_longest = "length"
    Sort_most_viewed = "views"


class SortPornstar(StrEnum):
    Sort_famous = ""
    Sort_most_viewed = "rank"
    Sort_most_videos = "movies"
    Sort_trending = "trending"


class SortChannel(StrEnum):
    Sort_ranking = ""
    Sort_name = "name"
    Sort_most_videos = "movies"
    Sort_most_viewed = "views"


class SortCategory(StrEnum):
    Sort_popular = ""
    Sort_name = "name"
    Sort_most_videos = "movies"
