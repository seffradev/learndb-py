from __future__ import annotations
import abc
import dataclasses
import os
from lark import Lark, logger, ast_utils, Transformer, v_args
from typing import Any, List, Union
from dataclasses import dataclass
from .visitor import Visitor

# logger.setLevel(logging.DEBUG)


WHITESPACE = ' '


class _Symbol(ast_utils.Ast):
    """
    The root of AST hierarchy
    """
    # NOTE: classes with preceding "_" will be skipped

    def accept(self, visitor: Visitor) -> Any:
        return visitor.visit(self)

    def is_virtual(self) -> bool:
        """
        Helper method to determine whether symbol/parsed
        rules' class is virtual, i.e. won't be materialized.
        Classes whose names begins with "_" are virtual.
        :return:
        """
        classname = self.__class__.__name__
        return classname.startswith("_")

    def get_prettychild(self, child, child_depth) -> list:
        """
        Get pretty printed child; calls different method depending on whether
        child is derived from _Symbol, Lark.Tree, or Lark.Token.

        :param child:
        :param child_depth:
        :return:
        """
        if hasattr(child, "prettyprint"):
            # part of Ast hierarchy
            val = child.prettyprint(depth=child_depth)
        elif hasattr(child, "pretty"):
            # part of autogenerated hierarchy
            preceding = WHITESPACE * child_depth
            formatted = f"{preceding}{child.pretty(preceding)}"
            val = [formatted]
        else:
            # token
            preceding = WHITESPACE * child_depth
            formatted = f"{preceding}{str(child)}"
            val = [formatted]
        return val

    def prettyprint(self, depth=0) -> List:
        """
        return a pretty printed string
        :return:
        """
        if hasattr(self, "asdict"):
            children = self.asdict()
        else:
            children = {key: getattr(self, key)
                        for key in dir(self)
                        if (not key.startswith("_") and not callable(getattr(self, key)))}
        lines = []

        child_depth = depth if self.is_virtual() else depth + 1
        preceding = WHITESPACE * depth
        if not self.is_virtual():
            classname = self.__class__.__name__
            lines.append(f'{preceding}{classname}:{os.linesep}')

        for key, value in children.items():
            child = getattr(self, key)
            if isinstance(child, list):
                # list
                for element in child:
                    lines.extend(self.get_prettychild(element, child_depth))
            else:
                # scalar
                lines.extend(self.get_prettychild(child, child_depth))

        return lines


@dataclass
class Program(_Symbol, ast_utils.AsList):
    statements: List[_Stmnt]


# is this even needed?
class _Stmnt(_Symbol):
    pass


@dataclass
class SelectStmnt(_Stmnt):
    select_clause: _Selectables
    from_clause: FromClause = None
    group_by_clause: Any = None
    having_clause: Any = None
    order_by_clause: Any = None
    limit_clause: Any = None


@dataclass
class _Selectables(_Symbol,  ast_utils.AsList):
    selections: List[Selectable]


@dataclass
class Selectable(_Symbol):
    item: Any


# defining class, allows me control how source is stored
class FromClauseX(_Symbol):
    def __init__(self, source: Any, where_clause: Any = None):
        self.source = source
        self.where_clause = where_clause


@dataclass
class FromClause(_Symbol):
    source: Joining
    # where clauses is nested in from, i.e. in a select
    # a where clause without a from clause is invalid
    where_clause: Any = None


@dataclass
class SingleSourceY(_Symbol):
    table_name: Any
    table_alias: Any = None


class SingleSource(_Symbol):
    def __init__(self, table_name, table_alias=None):
        self.table_name = table_name
        self.table_alias = table_alias


# nuke me?
@dataclass
class Joining(_Symbol):
    source: Union[ConditionedJoin, UnconditionedJoin]



@dataclass
class ConditionedJoinX(_Symbol):
    source: Any
    join_modifier: Any = None
    # since the join_modifier token is optional, I have to make `condition`
    # optional to make dataclass syntax work
    # the more correct approach would be to accept *args, and based on type of
    # arg, set the instance variable
    # the problem is that the grammar rule has optional symbols in the middle,
    # and the parsed args are positionally passed to these symbol classes

    # this actually causes a deeper problem, whereby symbols with optional
    # symbols can't easily be positionally assigned. One fix is to only accept
    # named params, or accept pos args, and map them to kw args based on rule name
    # perhaps I can use the annotation on arg to do mapping
    other_source: Any = None
    other_alias: Any = None
    condition: Any = None


def resolve_tokens(fields, tokens):
    """
    returns tokens mapped to fields (definition)
    the mapping is based on an exact name match
    return list of tokens mapped to names from token_def

    """
    resolved = [None] * len(fields)
    for i, field in enumerate(fields):
        # the match is based on an exact name match
        # either the rule exists as its own named tree
        # or the camelcase name of the class matches
        #if hasattr(token, "data"):
        #    pass
        #else:
        #    pass
        pass


        #field.name
        #if matches(field, token):
        #    resolved[i] = token
    return resolved


class Field:
    def __init__(self, name, optional=False, types=None):
        self.name = name
        self.optional = optional
        self.types = types


class ConditionedJoinY(_Symbol):
    def __new__(cls, *tokens):
        # maps tokens to fields
        # NOTE: it maybe possible to generate this from the grammar
        fields = [
            Field(name="source", optional=False, types=[SingleSource, Joining]),
            Field(name="join_modifier", optional=True, tree=""),
            Field(name="other_source", optional=False),
            Field(name="condition", optional=True)
        ]
        resolved = resolve_tokens(fields, tokens)
        # map args to tokens

        source = resolved[0]
        join_modifier = resolved[1]
        other_source = resolved[2]
        condition = resolved[3]
        return cls(source, join_modifier, other_source, condition)


class ConditionedJoinX(_Symbol):
    def __init__(self, source, join_modifier=None, other_source=None, condition=None):
        self.source = source
        self.join_modifier = join_modifier
        self.other_source = other_source
        self.condition = condition


@dataclass
class UnconditionedJoin(_Symbol):
    source: Any
    other_source: Any

