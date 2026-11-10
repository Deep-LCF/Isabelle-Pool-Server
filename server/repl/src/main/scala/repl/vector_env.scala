package repl

import isabelle._
import scala.collection.mutable
import scala.util.Random

/** Vectorised proof environments (from the original REPL prototype): duplicates of the
 *  current theory so one command can be stepped across N independent copies
 *  (`vectorise` / `vector_step` / `scalarise` on Backend_Chunk_Ops). Consumed
 *  only by the chunk-centric/small-step execution surface. */
class Vector_Env(vector_size: Int, current_thy: Option[Thy_Info]) {
  require(vector_size > 0, "Vector size must be positive")

  val original_to_duplicates: mutable.Map[Thy_Info, mutable.ListBuffer[Thy_Info]] =
    mutable.Map.empty
  val duplicates_to_original: mutable.Map[Thy_Info, Thy_Info] = mutable.Map.empty
  var current_duplicates: Option[List[Thy_Info]] = None

  def duplicate(original: Thy_Info): Thy_Info = {
    val duplicate_name = s"${original.name}_dup_${UUID.random_string()}"
    val duplicate = original.duplicate(duplicate_name)
    val dups = original_to_duplicates.getOrElseUpdate(original, mutable.ListBuffer.empty)
    dups += duplicate
    duplicates_to_original(duplicate) = original
    duplicate
  }

  def enter(original: Thy_Info): Unit =
    original_to_duplicates.get(original) match {
      case Some(duplicates) =>
        current_duplicates = Some(duplicates.toList)
      case None =>
        current_duplicates = Some(List.fill(vector_size)(duplicate(original)))
    }

  def scalarise(env_to_keep: Int): List[(Thy_Info, Thy_Info)] =
    original_to_duplicates.map { case (original, duplicates) =>
      val duplicate_to_keep = duplicates(env_to_keep)
      (original, duplicate_to_keep)
    }.toList
}
