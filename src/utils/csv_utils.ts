/**
 * Escapes a string value to prevent spreadsheet software from interpreting it as a formula.
 *
 * This function prepends a single quote (') to the string if it starts with
 * any character commonly recognized by spreadsheet applications (e.g., Excel, Google Sheets)
 * as a potential formula initiator. This ensures the cell's content is treated as literal text.
 *
 * @param value The value to potentially escape. It will be converted to a string representation.
 * @returns The escaped string, or the original string representation if no formula escaping is required.
 */
export function escapeCsvFormula(value: unknown): string {
  // Handle null or undefined values by returning an empty string.
  // This is a common practice for representing empty cells in CSV exports.
  if (value === null || value === undefined) {
    return '';
  }

  const stringValue = String(value);

  // Define a comprehensive list of characters that, when leading a cell's content,
  // can trigger formula interpretation in spreadsheet software.
  // This list includes standard formula starters as well as characters that could be
  // exploited or misinterpreted in specific spreadsheet contexts.
  const formulaStartChars = ['=', '+', '-', '@', '|', '%', '\t'];

  // Efficiently check if the string starts with any of the defined formula-triggering characters.
  // The `some` method allows for early exit as soon as a match is found.
  const startsWithFormulaChar = formulaStartChars.some(char => stringValue.startsWith(char));

  // If the string is identified as potentially being interpreted as a formula,
  // prepend it with a single quote. This is the widely accepted standard CSV escaping technique
  // to force spreadsheet programs to treat the cell's content as plain text.
  if (startsWithFormulaChar) {
    return `'${stringValue}`;
  }

  // If no formula-triggering character is found, the string does not require
  // formula-specific escaping and can be returned as is.
  // Note: This function is solely responsible for formula escaping. Other CSV formatting concerns,
  // such as enclosing values containing commas or double quotes, are outside its scope
  // and should be handled by a dedicated CSV serialization utility.
  return stringValue;
}