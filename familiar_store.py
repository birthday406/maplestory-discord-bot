import sqlite3
from pathlib import Path

from maple_data import (
    FAMILIAR_DOUBLE_PRIME_CHANCE,
    FAMILIAR_EPIC_POTENTIALS,
    FAMILIAR_UNIQUE_POTENTIALS,
)


class FamiliarExpectationStore:
    """가능한 퍼밀리어 두 줄 조합의 확률과 기대 횟수를 SQLite에 저장합니다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rebuild()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _rebuild(self) -> None:
        """봇 시작 때 현재 확률표의 모든 조합을 한 번 계산해 저장합니다."""
        unique_total = sum(rate for _, rate in FAMILIAR_UNIQUE_POTENTIALS)
        epic_total = sum(rate for _, rate in FAMILIAR_EPIC_POTENTIALS)
        unique = [
            (name, rate / unique_total) for name, rate in FAMILIAR_UNIQUE_POTENTIALS
        ]
        epic = [(name, rate / epic_total) for name, rate in FAMILIAR_EPIC_POTENTIALS]

        rows = []
        for first_name, first_probability in unique:
            for second_name, second_probability in epic:
                probability = (
                    first_probability
                    * (1 - FAMILIAR_DOUBLE_PRIME_CHANCE)
                    * second_probability
                )
                rows.append((first_name, second_name, 0, probability))
            for second_name, second_probability in unique:
                probability = (
                    first_probability
                    * FAMILIAR_DOUBLE_PRIME_CHANCE
                    * second_probability
                )
                rows.append((first_name, second_name, 1, probability))

        # 현재 조합과 같거나 더 희귀한 결과가 실제 추첨에서 나올 확률을 더합니다.
        sorted_probabilities = sorted(row[3] for row in rows)
        cumulative_probability = 0.0
        rarity_by_probability = {}
        for probability in sorted_probabilities:
            cumulative_probability += probability
            rarity_by_probability[probability] = cumulative_probability * 100
        stored_rows = [
            (
                first_name,
                second_name,
                double_prime,
                probability,
                1 / probability,
                rarity_by_probability[probability],
            )
            for first_name, second_name, double_prime, probability in rows
        ]

        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS familiar_expectations (
                    first_line TEXT NOT NULL,
                    second_line TEXT NOT NULL,
                    double_prime INTEGER NOT NULL,
                    probability REAL NOT NULL,
                    expected_attempts REAL NOT NULL,
                    rarity_percentile REAL NOT NULL,
                    PRIMARY KEY (first_line, second_line, double_prime)
                );
                DELETE FROM familiar_expectations;
                """
            )
            connection.executemany(
                """
                INSERT INTO familiar_expectations
                    (first_line, second_line, double_prime, probability,
                     expected_attempts, rarity_percentile)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                stored_rows,
            )

    def get(self, result: tuple[str, str, bool]) -> dict:
        """현재 두 줄 조합의 미리 계산된 기대값 한 건만 읽습니다."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT probability, expected_attempts, rarity_percentile
                FROM familiar_expectations
                WHERE first_line = ? AND second_line = ? AND double_prime = ?
                """,
                (result[0], result[1], int(result[2])),
            ).fetchone()
        return dict(row)
