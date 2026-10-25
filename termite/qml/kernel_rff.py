"""Emitter classifier: random Fourier features + ridge regression (one-vs-rest).

Random Fourier features approximate an RBF kernel machine in O(n * D) instead
of O(n^2), so it trains in milliseconds on a Pi and needs only numpy. Features
come from ``termite.sdr.features.feature_vector``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class RFFClassifier:
    def __init__(self, n_features: int = 512, gamma: float = 0.5, alpha: float = 1e-2, seed: int = 0) -> None:
        self.n_features = n_features
        self.gamma = gamma
        self.alpha = alpha
        self.seed = seed
        self.classes_: np.ndarray | None = None
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None
        self.W: np.ndarray | None = None
        self.b: np.ndarray | None = None
        self.coef_: np.ndarray | None = None

    def _map(self, X: np.ndarray) -> np.ndarray:
        assert self.W is not None and self.b is not None and self.mean_ is not None and self.std_ is not None
        Z = (np.asarray(X, dtype=np.float64) - self.mean_) / self.std_
        return np.sqrt(2.0 / self.n_features) * np.cos(Z @ self.W + self.b)

    def fit(self, X: np.ndarray, y: np.ndarray) -> RFFClassifier:
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y)
        if X.ndim != 2 or len(X) != len(y) or len(X) == 0:
            raise ValueError("X must be (n_samples, n_features) and match y")
        rng = np.random.default_rng(self.seed)
        self.mean_ = X.mean(axis=0)
        self.std_ = X.std(axis=0) + 1e-9
        d = X.shape[1]
        # RBF k(x, y) = exp(-gamma ||x - y||^2)  <=>  W ~ N(0, 2 gamma I)
        self.W = rng.normal(0.0, np.sqrt(2 * self.gamma), size=(d, self.n_features))
        self.b = rng.uniform(0, 2 * np.pi, size=self.n_features)
        self.classes_ = np.unique(y)
        Phi = self._map(X)
        Y = (y[:, None] == self.classes_[None, :]).astype(np.float64) * 2 - 1
        A = Phi.T @ Phi + self.alpha * np.eye(self.n_features)
        self.coef_ = np.linalg.solve(A, Phi.T @ Y)
        return self

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        if self.coef_ is None:
            raise RuntimeError("classifier is not fitted")
        out: np.ndarray = self._map(X) @ self.coef_
        return out

    def predict(self, X: np.ndarray) -> np.ndarray:
        assert self.classes_ is not None
        out: np.ndarray = self.classes_[np.argmax(self.decision_function(X), axis=1)]
        return out

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        s = self.decision_function(X)
        e = np.exp(s - s.max(axis=1, keepdims=True))
        out: np.ndarray = e / e.sum(axis=1, keepdims=True)
        return out

    def score(self, X: np.ndarray, y: np.ndarray) -> float:
        return float(np.mean(self.predict(X) == np.asarray(y)))

    def save(self, path: Path) -> None:
        if self.coef_ is None:
            raise RuntimeError("classifier is not fitted")
        np.savez_compressed(
            path, W=self.W, b=self.b, coef=self.coef_, classes=self.classes_, mean=self.mean_, std=self.std_,
            params=np.array([self.n_features, self.gamma, self.alpha, self.seed], dtype=np.float64),
        )

    @classmethod
    def load(cls, path: Path) -> RFFClassifier:
        with np.load(path, allow_pickle=False) as z:
            n, gamma, alpha, seed = z["params"]
            clf = cls(int(n), float(gamma), float(alpha), int(seed))
            clf.W, clf.b, clf.coef_ = z["W"], z["b"], z["coef"]
            clf.classes_, clf.mean_, clf.std_ = z["classes"], z["mean"], z["std"]
        return clf
