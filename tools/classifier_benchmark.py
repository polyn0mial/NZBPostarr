"""Measured accuracy baseline for NZBPostarr's name-level classifier.

Every case is a real-world-shaped release name with a label a human would agree
with. Run: prints per-bucket accuracy and every miss.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from logic.classify.names import classify_video_name  # noqa: E402

M, T = "Movie", "TV Show"

CASES = [
    # ---- easy movies -------------------------------------------------
    ("The.Matrix.1999.1080p.BluRay.x264-GRP", M, "easy"),
    ("Dune.Part.Two.2024.2160p.UHD.BluRay.x265-TEAM", M, "easy"),
    ("Oppenheimer.2023.1080p.WEB-DL.DDP5.1.H264-NTb", M, "easy"),
    # ---- easy TV -----------------------------------------------------
    ("Severance.S02E01.1080p.ATVP.WEB-DL.DDP5.1-NTb", T, "easy"),
    ("The.Bear.S03.COMPLETE.1080p.HULU.WEB-DL", T, "easy"),
    ("Breaking.Bad.S05E14.Ozymandias.720p.HDTV.x264", T, "easy"),
    # ---- movie titles containing numbers that look like SxxExx -------
    ("Ocean's.Eleven.2001.1080p.BluRay.x264", M, "number-title"),
    ("Se7en.1995.REMASTERED.1080p.BluRay", M, "number-title"),
    ("21.Jump.Street.2012.1080p.BluRay.x264", M, "number-title"),
    ("The.Hateful.Eight.2015.1080p.BluRay", M, "number-title"),
    ("300.2006.1080p.BluRay.x264", M, "number-title"),
    ("1917.2019.1080p.BluRay.x264-GRP", M, "year-as-title"),
    ("2012.2009.1080p.BluRay.x264", M, "year-as-title"),
    ("1408.2007.EXTENDED.1080p.BluRay", M, "year-as-title"),
    # ---- TV titles containing years ----------------------------------
    ("9-1-1.Lone.Star.S04E12.1080p.WEB-DL", T, "year-in-tv-title"),
    ("Dallas.2012.S01E01.720p.HDTV.x264", T, "year-in-tv-title"),
    ("Doctor.Who.2005.S01E01.720p.HDTV", T, "year-in-tv-title"),
    ("Hawaii.Five-0.2010.S01E01.720p.HDTV", T, "year-in-tv-title"),
    # ---- date-based episodes -----------------------------------------
    ("The.Daily.Show.2024.03.11.Guest.Name.1080p.WEB", T, "date-episode"),
    ("Jimmy.Kimmel.2023.11.02.1080p.WEB.h264", T, "date-episode"),
    # ---- alternate episode numbering ---------------------------------
    ("Some.Show.1x05.HDTV.XviD-GRP", T, "alt-numbering"),
    ("Another.Series.S02E03-E04.720p.HDTV.x264", T, "multi-episode"),
    ("Show.Name.Season.3.Episode.7.1080p.WEB", T, "verbose-numbering"),
    ("Series.Name.Part.2.1080p.WEB-DL", T, "part-numbering"),
    # ---- miniseries / limited ----------------------------------------
    ("Chernobyl.S01.LIMITED.SERIES.1080p.AMZN.WEB-DL", T, "miniseries"),
    ("Band.of.Brothers.2001.COMPLETE.MINISERIES.1080p.BluRay", T, "miniseries"),
    # ---- movie collections vs season packs ---------------------------
    ("The.Lord.of.the.Rings.Trilogy.2001-2003.1080p.BluRay", M, "collection"),
    ("Back.to.the.Future.Complete.Collection.1080p.BluRay", M, "collection"),
    ("Alien.Quadrilogy.1979-1997.1080p.BluRay", M, "collection"),
    # ---- sports / events (not scripted TV) ---------------------------
    ("UFC.300.Main.Event.1080p.WEB.h264", T, "sports"),
    ("NFL.2024.Week.5.Chiefs.vs.Saints.1080p", T, "sports"),
    # ---- stand-up / documentary --------------------------------------
    ("Comedian.Name.Special.Title.2022.1080p.NF.WEB-DL", M, "standup"),
    ("Planet.Earth.III.S01E01.1080p.iP.WEB-DL", T, "doc-series"),
    ("Free.Solo.2018.1080p.BluRay.x264", M, "documentary-film"),
    # ---- anime -------------------------------------------------------
    ("[SubGroup].Attack.on.Titan.-.137.[1080p].[HEVC]", T, "anime-absolute"),
    ("Frieren.S01E12.1080p.CR.WEB-DL.AAC2.0.H.264", T, "anime-sxxexx"),
    # ---- foreign / unusual tokens ------------------------------------
    ("Das.Boot.1981.DIRECTORS.CUT.1080p.BluRay", M, "foreign"),
    ("Le.Fabuleux.Destin.d.Amelie.Poulain.2001.1080p.BluRay", M, "foreign"),
    ("Squid.Game.S01E01.KOREAN.1080p.NF.WEB-DL", T, "foreign-tv"),
    # ---- tricky: movie with a 'season'-like word ---------------------
    ("Season.of.the.Witch.2011.1080p.BluRay.x264", M, "trap-word"),
    ("The.Last.Season.2014.1080p.WEB-DL", M, "trap-word"),
    ("Silent.Hill.Revelation.2012.1080p.BluRay", M, "trap-word"),
    # ---- tricky: TV with a movie-ish name ----------------------------
    ("Fargo.S04E01.1080p.AMZN.WEB-DL.DDP5.1-NTb", T, "tv-movie-name"),
    ("Westworld.S03E08.1080p.AMZN.WEB-DL", T, "tv-movie-name"),
]


def main():
    by_bucket = {}
    misses = []
    for name, want, bucket in CASES:
        got = classify_video_name(name)
        ok = got == want
        hit, tot = by_bucket.get(bucket, (0, 0))
        by_bucket[bucket] = (hit + (1 if ok else 0), tot + 1)
        if not ok:
            misses.append((bucket, name, got, want))

    total = len(CASES)
    correct = total - len(misses)
    print(f"OVERALL: {correct}/{total} = {correct / total * 100:.1f}%\n")
    print("by bucket:")
    for bucket, (hit, tot) in sorted(by_bucket.items(), key=lambda kv: (kv[1][0] / kv[1][1], kv[0])):
        flag = "  <-- weak" if hit < tot else ""
        print(f"  {bucket:20s} {hit}/{tot}{flag}")
    print(f"\nMISSES ({len(misses)}):")
    for bucket, name, got, want in misses:
        print(f"  [{bucket}] {name}")
        print(f"      got {got!r}, expected {want!r}")


if __name__ == "__main__":
    main()
