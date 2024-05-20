const urlPrefix = "ex-twitter--db-client-api";
const urlSuffix =
  import.meta.env.VITE_DEV_BACKEND === "true" ? "-dev.modal.run" : ".modal.run";

const baseUrl = `https://${urlPrefix}${urlSuffix}`;

const fetchData = async (url) => {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error("Error fetching data:", error);
    return { error };
  }
};

const getFeed = async () => {
  const url = `${baseUrl}/tweets/`;
  return await fetchData(url);
};

const getUser = async (userId) => {
  const url = `${baseUrl}/users/${userId}/`;
  return await fetchData(url);
};

const getUserTweets = async (userId) => {
  const url = `${baseUrl}/users/${userId}/tweets/`;
  return await fetchData(url);
};

const getUserProfile = async (userId) => {
  try {
    const user = await getUser(userId);
    const tweets = await getUserTweets(userId);
    return { ...user, tweets };
  } catch (error) {
    console.error("Error fetching user profile:", error);
    return { error };
  }
};

export { getFeed, getUser, getUserTweets, getUserProfile };
